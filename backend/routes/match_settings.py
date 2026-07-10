"""MATCH ingestion — Settings (admin-gated singleton) routes.

Adapted from source app ``backend/routers/settings.py``
(Simplimeta/Appdirect-Order-POC1 @ feature/poc-to-prod).

Settings is a **singleton** document keyed by ``{"_singleton": True}`` in the
``match_settings`` collection. There is no "create" — GET returns Pydantic
defaults if the doc is absent, and PUT upserts via deep-merge so partial
updates (e.g. only ``crm`` or only ``appdirect``) never wipe sibling blocks.

Encrypted-field wire protocol (the AEGISAXIOM-wide security guarantee for MATCH):

  - The four at-rest-encrypted fields are::

        n8n.shared_auth_token
        appdirect.bearer_token
        crm.sql.service_password
        crm.api.api_key

  - **GET** masks every encrypted field with :data:`MASKED_PASSWORD_SENTINEL`
    (12 U+2022 bullets) if a ciphertext is present, or the empty string if
    not. Plaintext and ciphertext are never returned to the client.

  - **PUT** treats empty/sentinel as "leave existing ciphertext alone" and
    encrypts anything else as new plaintext (see :func:`_resolve_password_on_put`).

  - **Deep-merge on PUT** is mandatory — a partial body must preserve every
    sibling block. Lifted verbatim from MATCH source as :func:`_deep_merge`.

The ``n8n.callback_url`` field is auto-derived per-deploy from the
``MATCH_PUBLIC_BASE_URL`` / ``FRONTEND_URL`` env var when it's blank on GET,
so operators don't have to hand-edit it after a Cloud Run promotion.

Test endpoints (``test-sql``, ``test-appdirect``, ``test-n8n``) re-issue the
"Test Connection" buttons in the Settings UI. ``test-sql`` is a stub pending
the live SQL helper landing in the MATCH service layer; ``test-appdirect``
proxies through :class:`AppDirectClient.lookup_customer`; ``test-n8n``
proxies through :class:`N8nClient.ping`.

``appdirect-lookups`` syncs the AppDirect enum lookup tables and caches them
under ``settings.appdirect.cached_lookups`` for the Settings UI's dropdowns.
A refresh kicks off if the cache is older than one hour OR missing.
"""
from __future__ import annotations

import logging
import os
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from services.field_registry import (
    register_module_config,
    register_module_events,
)
from services.ingestion.route_helpers import crud_envelope
from services.match.appdirect_client import AppDirectClient
from services.match.crypto import (
    MASKED_PASSWORD_SENTINEL,
    encrypt_value,
)
from services.match.models import (
    Settings,
    SettingsUpdate,
    TestConnectionResponse,
)
from services.match.n8n_client import N8nClient

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Registration (runs at module import)
# ---------------------------------------------------------------------------
# Singleton — no id_field. Use "Custom" category to group MATCH modules in the
# Admin GUI alongside its sibling entities.
register_module_config("match", "match_settings", None, "Custom")
register_module_events(
    "match",
    "settings",
    ["updated", "n8n_tested", "appdirect_tested", "sql_tested"],
)
# NOTE: register_fields() is intentionally omitted for the singleton. The
# nested sub-blocks (crm, appdirect, n8n, schedule) are opaque JSON to the
# policy/workflow engines; policies that need to target subkeys reference
# them by JSON-path via the policy engine's path matcher.


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SINGLETON_FILTER: Dict[str, Any] = {"_singleton": True}

# AppDirect lookup cache TTL — refresh if older than this on GET /appdirect-lookups.
_LOOKUP_CACHE_TTL = timedelta(hours=1)

# Default fallback for callback_url auto-derivation when neither
# MATCH_PUBLIC_BASE_URL nor FRONTEND_URL is configured.
_DEFAULT_LOCAL_BASE_URL = "http://localhost:8002"

# Encrypted field paths within the settings doc. Each tuple is the chain of
# dict keys leading to the encrypted value. Used by the mask/resolve helpers
# below so we never miss one when adding a new sensitive field.
_ENCRYPTED_PATHS: tuple = (
    ("n8n", "shared_auth_token"),
    ("appdirect", "bearer_token"),
    ("crm", "sql", "service_password"),
    ("crm", "api", "api_key"),
)


# ---------------------------------------------------------------------------
# Helpers — ported from MATCH source backend/routers/settings.py
# ---------------------------------------------------------------------------
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _mask_for_get(stored_ciphertext: str) -> str:
    """Stored value is ciphertext (or empty). Return the sentinel if a value
    is configured, else the empty string. Plaintext is NEVER returned."""
    return MASKED_PASSWORD_SENTINEL if stored_ciphertext else ""


def _resolve_password_on_put(incoming: str, existing_ciphertext: str) -> str:
    """Decide what to store as ciphertext on PUT for an encrypted field.

    - Incoming is None / empty / equals the sentinel → preserve existing
      ciphertext (the user did not retype the value).
    - Otherwise → encrypt the incoming plaintext.
    """
    if incoming is None or incoming == "" or incoming == MASKED_PASSWORD_SENTINEL:
        return existing_ciphertext or ""
    return encrypt_value(incoming)


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge *overlay* into *base* and return the result.

    Merge semantics:
        - Keys only in *base*    → PRESERVED.
        - Keys only in *overlay* → ADDED.
        - Keys in BOTH and both dicts → recurse.
        - Keys in BOTH otherwise → overlay wins (scalar/list overwrite).

    Crucial guarantee: a partial PUT that omits ``appdirect`` entirely
    leaves the whole ``appdirect`` block (including ``cached_lookups``,
    ``base_url``, ``bearer_token``, ``defaults.*``) completely untouched.
    """
    result = deepcopy(base)
    for key, overlay_val in overlay.items():
        base_val = result.get(key)
        if isinstance(base_val, dict) and isinstance(overlay_val, dict):
            result[key] = _deep_merge(base_val, overlay_val)
        else:
            result[key] = deepcopy(overlay_val)
    return result


def _walk(doc: Dict[str, Any], path: tuple) -> Any:
    """Walk a tuple-path into a nested dict, returning "" if any segment is missing."""
    cur: Any = doc
    for key in path:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(key, "")
    return cur if cur is not None else ""


def _set_at(doc: Dict[str, Any], path: tuple, value: Any) -> None:
    """Set ``value`` at the dotted path inside ``doc``, creating dicts as needed."""
    cur: Dict[str, Any] = doc
    for key in path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[path[-1]] = value


def _derive_callback_url() -> str:
    """Auto-derive the n8n inbound callback URL from the deploy env.

    Order: ``MATCH_PUBLIC_BASE_URL`` → ``FRONTEND_URL`` → localhost fallback.
    The path is always ``/api/match/webhooks/row-processed`` — that's the
    canonical inbound contract owned by ``match_webhooks.py``.
    """
    base = (
        os.getenv("MATCH_PUBLIC_BASE_URL")
        or os.getenv("FRONTEND_URL")
        or _DEFAULT_LOCAL_BASE_URL
    )
    return f"{base.rstrip('/')}/api/match/webhooks/row-processed"


def _mask_response(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow-cloned doc with every encrypted field masked and the
    callback_url auto-derived if blank. Strips Mongo ``_id`` and ``_singleton``
    markers as well."""
    out = deepcopy(doc)
    out.pop("_id", None)
    out.pop("_singleton", None)

    for path in _ENCRYPTED_PATHS:
        stored = _walk(out, path)
        _set_at(out, path, _mask_for_get(stored if isinstance(stored, str) else ""))

    # Auto-derive callback_url when blank — operators shouldn't have to set
    # it manually per deploy.
    n8n_block = out.get("n8n")
    if isinstance(n8n_block, dict) and not n8n_block.get("callback_url"):
        n8n_block["callback_url"] = _derive_callback_url()

    return out


async def _load_singleton_doc(db) -> Dict[str, Any]:
    """Return the raw settings doc (with ciphertext intact) or Pydantic defaults
    as a plain dict.

    Used by routes that need to deep-merge or mask the doc (GET/PUT, lookups).
    Routes that hand the settings to a client (AppDirectClient / N8nClient)
    should use :func:`_load_singleton` instead, which returns a validated
    Pydantic ``Settings`` instance — the shape those clients require.
    """
    doc = await db["match_settings"].find_one(_SINGLETON_FILTER)
    if not doc:
        return Settings().model_dump(mode="json")
    return doc


async def _load_singleton(db) -> Settings:
    """Return the settings singleton as a validated ``Settings`` model.

    AppDirectClient / N8nClient access attributes (``settings.appdirect.*``,
    ``settings.n8n.*``) in their constructors — a raw dict would AttributeError
    on the first call. Centralizing the model_validate cast here keeps every
    test/lookup handler honest.
    """
    raw = await db["match_settings"].find_one(_SINGLETON_FILTER)
    if not raw:
        return Settings()
    # Strip Mongo internals before validation so we never confuse the model.
    clean = {k: v for k, v in raw.items() if k not in ("_id", "_singleton")}
    # Mongo round-trips ``updated_at`` as a native ``datetime``; the Settings
    # model declares it as ``Optional[str]`` so model_validate would fail.
    # Coerce to ISO 8601 here — keeps the model surface stable for callers
    # that JSON-serialize the result without further conversion.
    if isinstance(clean.get("updated_at"), datetime):
        clean["updated_at"] = clean["updated_at"].isoformat()
    return Settings.model_validate(clean)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "",
    dependencies=[Depends(require_ai_permission("match.settings.view"))],
)
async def get_settings(current_user=Depends(get_current_user)):
    """Load the settings singleton, masked for the wire.

    Returns Pydantic defaults if the singleton doesn't exist yet, so the
    Settings UI can render a fresh form without a 404 dance.
    """
    if not has_perm(current_user, "match.settings.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    # GET needs the raw dict (with ciphertext) so _mask_response can replace
    # encrypted paths with the sentinel. Pydantic-casted Settings would have
    # already lost the masking shape.
    doc = await _load_singleton_doc(db)
    return _mask_response(doc)


@router.put(
    "",
    dependencies=[Depends(require_ai_permission("match.settings.edit"))],
)
async def update_settings(
    payload: SettingsUpdate,
    request: Request,
    current_user=Depends(get_current_user),
):
    """Deep-merge the incoming payload into the singleton, preserving siblings.

    Encrypted-field rule: each of the four encrypted paths uses
    :func:`_resolve_password_on_put`. Sentinel/empty preserves existing
    ciphertext; anything else re-encrypts.

    Returns the post-merge settings with masked encrypted fields — same
    shape as GET so the frontend can render the response directly.
    """
    if not has_perm(current_user, "match.settings.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    existing = await db["match_settings"].find_one(_SINGLETON_FILTER) or {}

    # Only what the client explicitly sent — Pydantic's exclude_unset prevents
    # defaults from leaking into the deep-merge and clobbering sibling blocks.
    incoming_doc: Dict[str, Any] = payload.model_dump(exclude_unset=True)
    incoming_doc["updated_at"] = _utc_now()

    # Resolve each encrypted field BEFORE the merge, only when the client
    # actually included that exact path in the payload.
    for path in _ENCRYPTED_PATHS:
        # Did the incoming payload include this exact path?
        cur: Any = incoming_doc
        present = True
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                present = False
                break
            cur = cur[key]
        if not present:
            continue

        existing_ciphertext = _walk(existing, path)
        resolved = _resolve_password_on_put(
            cur if isinstance(cur, str) else "",
            existing_ciphertext if isinstance(existing_ciphertext, str) else "",
        )
        _set_at(incoming_doc, path, resolved)

    existing_clean = {
        k: v for k, v in existing.items() if k not in ("_id", "_singleton")
    }
    merged = _deep_merge(existing_clean, incoming_doc)
    merged["_singleton"] = True

    async with crud_envelope(
        module="match",
        entity="settings",
        action="updated",
        resource_id="_singleton",
        current_user=current_user,
        db=db,
        before=existing_clean,
        request=request,
    ) as ctx:
        await db["match_settings"].replace_one(
            _SINGLETON_FILTER, merged, upsert=True
        )
        # Stash a masked copy in the audit `after` so we never write plaintext
        # or ciphertext into the audit log.
        ctx.set_after(_mask_response(merged))

    return _mask_response(merged)


@router.post(
    "/test-sql",
    dependencies=[Depends(require_ai_permission("match.settings.edit"))],
)
async def test_sql_connection(
    request: Request,
    current_user=Depends(get_current_user),
) -> TestConnectionResponse:
    """Test the configured SQL connection.

    NOTE: this is a v1 stub. The source app's ``test_sql_connection``
    likewise returns a placeholder until the live SQL helper lands; we
    preserve that behavior here so the Settings UI button still wires
    cleanly. TODO: replace with a real connectivity probe once the
    ``services.match.sql_client`` module is implemented.
    """
    if not has_perm(current_user, "match.settings.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")

    # Fire-and-forget event so admins can see "tested" attempts in the audit
    # log even while the implementation is stubbed.
    db = get_db()
    async with crud_envelope(
        module="match",
        entity="settings",
        action="sql_tested",
        resource_id="_singleton",
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        ctx.set_after({"status": "stub"})

    return TestConnectionResponse(
        status="success",
        message="SQL test stub — live SQL probe not yet implemented",
        tables_verified=[],
    )


@router.post(
    "/test-appdirect",
    dependencies=[Depends(require_ai_permission("match.settings.edit"))],
)
async def test_appdirect_connection(
    request: Request,
    current_user=Depends(get_current_user),
) -> TestConnectionResponse:
    """Probe the AppDirect API by issuing a cheap lookup against the configured
    base URL + bearer token. Latency is reported in the message; the
    underlying :class:`AppDirectClient` logs the call to
    ``match_appdirect_calls`` via its own internal call-logger.
    """
    if not has_perm(current_user, "match.settings.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    settings_obj = await _load_singleton(db)

    # Explicit config validation BEFORE invoking the client. `lookup_customer`
    # short-circuits to dry-run when `live_post_enabled=False` and silently
    # returns None, which previously masked unconfigured AppDirect as a
    # passing test. The button must tell the truth about the configured state.
    ad = settings_obj.appdirect
    if not (ad.base_url or "").strip():
        return TestConnectionResponse(
            status="error",
            message="AppDirect base URL is not configured. Set it in Settings -> AppDirect and save.",
            tables_verified=[],
        )
    if not (ad.bearer_token or "").strip():
        return TestConnectionResponse(
            status="error",
            message="AppDirect bearer token is not configured. Set it in Settings -> AppDirect and save.",
            tables_verified=[],
        )

    start = time.monotonic()
    elapsed_ms = 0
    try:
        client = AppDirectClient(
            settings_obj, db=db, actor=current_user.user_id
        )
        # Force a real HTTP probe regardless of `live_post_enabled` so the
        # operator gets a true round-trip latency. The AppDirect Provider
        # Sales API has no dedicated `/ping` or `/version` GET, so we probe
        # `Customers/v2/0/locations` — customer id 0 cannot exist, so:
        #   * 2xx / 404 => server reachable, auth valid (success)
        #   * 401 / 403 => auth rejected
        #   * 5xx       => server error
        #   * conn error => unreachable
        endpoint = "/api/services/app/Customers/v2/0/locations"
        call = await client._live_request(  # noqa: SLF001 — intentional reach
            "GET", endpoint, json_body=None
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        rs = call.response_status or 0
        if call.error and rs == 0:
            result = TestConnectionResponse(
                status="error",
                message=f"AppDirect unreachable: {call.error}",
                latency_ms=elapsed_ms,
                tables_verified=[],
            )
        elif rs in (401, 403):
            result = TestConnectionResponse(
                status="error",
                message=f"AppDirect rejected the bearer token (HTTP {rs}). Re-paste the token without 'Bearer ' prefix.",
                latency_ms=elapsed_ms,
                tables_verified=[],
            )
        elif rs >= 500:
            result = TestConnectionResponse(
                status="error",
                message=f"AppDirect returned HTTP {rs} (server-side error).",
                latency_ms=elapsed_ms,
                tables_verified=[],
            )
        elif 200 <= rs < 500:
            # 2xx, 3xx, and other 4xx (incl. 404 for the probe id) all mean
            # the API responded and the token was accepted.
            result = TestConnectionResponse(
                status="success",
                message=f"Connected to AppDirect ({elapsed_ms} ms, HTTP {rs} on probe)",
                latency_ms=elapsed_ms,
                tables_verified=[],
            )
        else:
            result = TestConnectionResponse(
                status="error",
                message=f"AppDirect probe returned unexpected HTTP {rs}.",
                latency_ms=elapsed_ms,
                tables_verified=[],
            )
    except HTTPException as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result = TestConnectionResponse(
            status="error",
            message=str(exc.detail),
            latency_ms=elapsed_ms,
            tables_verified=[],
        )
    except Exception as exc:  # noqa: BLE001 — surface the friendly message
        elapsed_ms = int((time.monotonic() - start) * 1000)
        # NEVER include the bearer token in the error message. AppDirectClient
        # is expected to keep secrets out of its raised exception strings;
        # we log the exception type at info-level (not the str) for safety.
        logger.info(
            "AppDirect test failed after %s ms with %s",
            elapsed_ms,
            type(exc).__name__,
        )
        result = TestConnectionResponse(
            status="error",
            message=f"AppDirect test failed: {exc}",
            latency_ms=elapsed_ms,
            tables_verified=[],
        )

    async with crud_envelope(
        module="match",
        entity="settings",
        action="appdirect_tested",
        resource_id="_singleton",
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        ctx.set_after(
            {"status": result.status, "latency_ms": elapsed_ms}
        )

    return result


@router.post(
    "/test-n8n",
    dependencies=[Depends(require_ai_permission("match.settings.edit"))],
)
async def test_n8n_connection(
    request: Request,
    current_user=Depends(get_current_user),
) -> TestConnectionResponse:
    """Ping the configured n8n trigger webhook URL using the shared bearer token.

    Backs the "Test Connection" button on the n8n settings section. The
    :class:`N8nClient.ping` helper returns ``(ok, message, latency_ms)`` —
    the friendly message + latency are surfaced verbatim so admins can see
    HTTP status, timeout, or auth-mismatch hints without digging through
    backend logs.
    """
    if not has_perm(current_user, "match.settings.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    settings_obj = await _load_singleton(db)

    start = time.monotonic()
    try:
        client = N8nClient(settings_obj)
        ping_result = await client.ping()
        elapsed_ms = int((time.monotonic() - start) * 1000)
        # N8nClient.ping() returns {ok, status_code, latency_ms, hint?}. The
        # ``hint`` carries the actionable operator diagnostic on non-success
        # (e.g. "n8n returned 403 — auth header didn't match..."). Surface it
        # verbatim so the Settings UI can render it; fall back to a default
        # message only when no hint was supplied.
        if isinstance(ping_result, dict):
            ok = bool(ping_result.get("ok"))
            hint = ping_result.get("hint")
            status_code = ping_result.get("status_code")
            reported_latency = ping_result.get("latency_ms", elapsed_ms)
        else:
            ok = bool(getattr(ping_result, "ok", False))
            hint = getattr(ping_result, "hint", None)
            status_code = getattr(ping_result, "status_code", None)
            reported_latency = getattr(ping_result, "latency_ms", elapsed_ms)

        latency_ms = int(reported_latency) if reported_latency is not None else elapsed_ms
        default_msg = (
            "n8n ping ok"
            if ok
            else f"n8n ping failed (HTTP {status_code if status_code is not None else 'unknown'})"
        )
        message = hint or default_msg
        result = TestConnectionResponse(
            status="success" if ok else "error",
            message=f"{message} ({latency_ms} ms)",
            latency_ms=latency_ms,
            tables_verified=[],
        )
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "n8n test failed after %s ms with %s",
            elapsed_ms,
            type(exc).__name__,
        )
        result = TestConnectionResponse(
            status="error",
            message=f"n8n test failed: {exc}",
            tables_verified=[],
        )

    async with crud_envelope(
        module="match",
        entity="settings",
        action="n8n_tested",
        resource_id="_singleton",
        current_user=current_user,
        db=db,
        request=request,
    ) as ctx:
        ctx.set_after({"status": result.status, "latency_ms": elapsed_ms})

    return result


@router.get(
    "/appdirect-lookups",
    dependencies=[Depends(require_ai_permission("match.settings.view"))],
)
async def get_appdirect_lookups(
    request: Request,
    current_user=Depends(get_current_user),
):
    """Return AppDirect lookup tables (cached). Refresh if stale or missing.

    The cache lives under ``settings.appdirect.cached_lookups`` and is
    timestamped via ``settings.appdirect.lookups_fetched_at``. A refresh
    kicks off if the cache is older than :data:`_LOOKUP_CACHE_TTL` or
    absent. The Settings UI calls this on mount so the AppDirect enum
    dropdowns populate without operator intervention.
    """
    if not has_perm(current_user, "match.settings.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    # Load both shapes: the raw dict is needed for the cache read and the
    # deep-merge below; the Pydantic Settings is needed by AppDirectClient.
    settings_doc = await _load_singleton_doc(db)
    settings_obj = await _load_singleton(db)
    appdirect_block = settings_doc.get("appdirect") or {}
    cached = appdirect_block.get("cached_lookups") or {}
    fetched_at_raw = appdirect_block.get("lookups_fetched_at")

    # Parse the timestamp tolerantly — it may be an ISO string from a prior
    # write, or a datetime if motor decoded it natively.
    fetched_at: datetime | None = None
    if isinstance(fetched_at_raw, datetime):
        fetched_at = fetched_at_raw
    elif isinstance(fetched_at_raw, str):
        try:
            fetched_at = datetime.fromisoformat(fetched_at_raw.replace("Z", "+00:00"))
        except ValueError:
            fetched_at = None

    needs_refresh = (
        not cached
        or fetched_at is None
        or (_utc_now() - (fetched_at if fetched_at.tzinfo else fetched_at.replace(tzinfo=timezone.utc))) > _LOOKUP_CACHE_TTL
    )

    if needs_refresh:
        try:
            client = AppDirectClient(
                settings_obj, db=db, actor=current_user.user_id
            )
            fresh = await client.fetch_lookup_tables()
            if isinstance(fresh, dict) and fresh:
                # Merge over any existing cache so a partial-failure refresh
                # never wipes lookups we already have.
                merged_cache = {**cached, **fresh}
                now = _utc_now()
                existing_clean = {
                    k: v
                    for k, v in (settings_doc or {}).items()
                    if k not in ("_id", "_singleton")
                }
                updated = _deep_merge(
                    existing_clean,
                    {
                        "appdirect": {
                            "cached_lookups": merged_cache,
                            "lookups_fetched_at": now,
                        },
                        "updated_at": now,
                    },
                )
                updated["_singleton"] = True

                async with crud_envelope(
                    module="match",
                    entity="settings",
                    action="updated",
                    resource_id="_singleton",
                    current_user=current_user,
                    db=db,
                    before=existing_clean,
                    request=request,
                ) as ctx:
                    await db["match_settings"].replace_one(
                        _SINGLETON_FILTER, updated, upsert=True
                    )
                    ctx.set_after(_mask_response(updated))

                cached = merged_cache
        except Exception as exc:  # noqa: BLE001
            # Refresh failed — fall back to whatever cache we had. NEVER raise
            # here; the Settings UI is allowed to render with stale lookups.
            logger.warning(
                "AppDirect lookup refresh failed (%s); returning cached lookups",
                type(exc).__name__,
            )

    return cached
