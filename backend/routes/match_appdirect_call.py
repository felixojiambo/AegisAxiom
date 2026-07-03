"""MATCH ingestion — AppDirect Call (push log) entity routes.

Adapted from source app ``backend/routers/appdirect.py``
(Simplimeta/Appdirect-Order-POC1 @ feature/poc-to-prod) — specifically the
``GET /api/rows/{row_id}/appdirect-calls`` endpoint and the
``_write_call_log`` storage shape.

An AppDirect call log captures a single attempt to push a matched row out to
the AppDirect Provider Sales API. Each log row records the request body,
redacted request headers, response status + body, latency, error (if any),
mode (dry_run vs live), and the actor who triggered the push. The reviewer
UI surfaces these logs in the row detail drawer to debug failures and audit
what was sent.

Router prefix (mounted in server.py): ``/api/match/rows``
This is intentionally the SAME prefix as ``match_row``'s router — the two
share URL space. FastAPI allows this because the paths within each don't
collide. The appdirect-call sub-resource always lives at
``/{row_id}/appdirect-calls``.

Write semantics:
    The collection is **read-only via the API surface**. There is no POST,
    PUT, or DELETE here. Writes are performed by
    ``services.match.appdirect_client.AppDirectClient`` (Thread 1A) directly
    against ``match_appdirect_calls`` during a push attempt — the audit
    record is a side-effect of the HTTP call, not a user-triggered CRUD.

    The ``logged`` event is fired by AppDirectClient (not by this router),
    but we register it here so the policy engine sees the full event
    surface for the entity.

Security note:
    ``request_headers_redacted`` arrives in the collection already masked
    (the Authorization bearer token is replaced with a sentinel before the
    log doc is ever persisted). This router passes the field through
    verbatim — DO NOT attempt to "unmask" or transform it on read.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from services.field_registry import (
    register_fields,
    register_module_config,
    register_module_events,
)
# NOTE: imported for side-effects — confirms Thread 1A's models module loads
# and that ``AppDirectCall`` is defined as an entity. Not currently used at
# runtime here (no construction happens in this read-only router) but the
# import keeps the dependency explicit and gives us an early failure if
# Thread 1A drifts on the model name.
from services.match.models import AppDirectCall  # noqa: F401

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Field / event / module registration (runs at module import)
# ---------------------------------------------------------------------------
# The 13 fields below mirror ``.ingestion/mappings/match.json::entities.
# appdirect_call.fields``. Source field ``id`` is registered as ``call_id``
# (the AEGISAXIOM canonical ID field for this entity).
register_fields(
    "match",
    "appdirect_call",
    [
        {"name": "call_id", "type": "string", "label": "Call ID"},
        {"name": "row_id", "type": "string", "label": "Row ID"},
        {"name": "session_id", "type": "string", "label": "Session ID"},
        {"name": "endpoint", "type": "string", "label": "Endpoint"},
        {"name": "request_body", "type": "json", "label": "Request Body"},
        {
            "name": "request_headers_redacted",
            "type": "json",
            "label": "Request Headers (redacted)",
        },
        {
            "name": "response_status",
            "type": "number",
            "label": "Response Status",
        },
        {"name": "response_body", "type": "json", "label": "Response Body"},
        {"name": "latency_ms", "type": "number", "label": "Latency (ms)"},
        {"name": "error", "type": "string", "label": "Error"},
        {
            "name": "mode",
            "type": "string",
            "label": "Mode",
            "options": [
                {"value": "dry_run", "label": "Dry Run"},
                {"value": "live", "label": "Live"},
            ],
        },
        {"name": "actor", "type": "string", "label": "Actor"},
        {"name": "created_at", "type": "date", "label": "Created At"},
    ],
)
register_module_config(
    "match", "match_appdirect_calls", "call_id", "Custom"
)
# Only ``logged`` is emitted — and it's emitted by AppDirectClient on write,
# not by this router. There is no API-driven create/update/delete.
register_module_events("match", "appdirect_call", ["logged"])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/{row_id}/appdirect-calls",
    dependencies=[Depends(require_ai_permission("match.appdirect_call.view"))],
)
async def list_appdirect_calls_for_row(
    row_id: str,
    current_user=Depends(get_current_user),
):
    """Return all AppDirect API call log entries for a row, newest first.

    Used by the row detail drawer to show the push history (which endpoints
    were hit, response codes, latencies, redacted headers, raw response
    bodies) so the reviewer can debug a failed push without paging through
    backend logs.

    Capped at 200 to keep drawer payloads bounded — a single row should
    never accumulate hundreds of push attempts under normal use; if it has,
    the cap signals "something is wrong, look at logs directly."

    Returns the raw call docs verbatim; ``request_headers_redacted`` arrives
    already masked from the writer side and is NOT re-processed here.
    """
    if not has_perm(current_user, "match.appdirect_call.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    docs = (
        await db["match_appdirect_calls"]
        .find({"row_id": row_id}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(200)
    )
    return docs
