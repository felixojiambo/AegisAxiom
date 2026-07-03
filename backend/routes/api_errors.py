"""Frontend error forwarder — v1.3 Wave 3.5 fix-up.

Sibling to `services/observability.py`'s GCP Error Reporting integration.
The frontend's `errorReporting.js` POSTs uncaught exceptions and React
ErrorBoundary catches here so they end up in the same Error Reporting
project as the backend's own exceptions, with correlated user_id and
URL context.

API contract
------------
POST /api/errors

Request body (JSON):
    {
        "message":     str   (required) — exception message / summary
        "stack":       str   (optional) — stack trace text
        "url":         str   (optional) — page URL where the error fired
        "user_agent":  str   (optional) — navigator.userAgent
        "context":     dict  (optional) — arbitrary correlation fields
        "severity":    str   (optional) — "error" | "warning" | "info"
                                          (default: "error")
    }

Response:
    200 {"ok": True}            on success
    401                          if not authenticated
    422                          if message is missing / empty

Security notes
--------------
- Requires auth — anonymous error reporting is not supported. This is
  intentional: it prevents random internet traffic from polluting our
  Error Reporting bucket and gives every report a known user_id for
  correlation.
- The message is truncated to 4 KiB before forwarding to avoid log-payload
  abuse; the stack trace to 16 KiB. These are well above legitimate
  client-side error sizes.
- The endpoint is rate-limited via slowapi at 30 req/min per client IP
  (`@limiter.limit("30/minute")`). Comfortably above legitimate uncaught-
  exception bursts on a single page load, well below abuse thresholds.
  The limiter is wired through the application-level slowapi registration
  in server.py.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from auth_helper import get_current_user
from rate_limiter import limiter
from services.observability import report_exception

logger = logging.getLogger(__name__)

router = APIRouter()

# Truncation guards — prevent a misbehaving client from filling our error
# bucket with multi-MB payloads.
_MAX_MESSAGE_LEN = 4 * 1024
_MAX_STACK_LEN = 16 * 1024
_MAX_URL_LEN = 2 * 1024
_MAX_USER_AGENT_LEN = 1 * 1024
_VALID_SEVERITIES = {"error", "warning", "info", "critical", "debug"}


class FrontendError(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str = Field(..., min_length=1, max_length=_MAX_MESSAGE_LEN)
    stack: Optional[str] = Field(default=None, max_length=_MAX_STACK_LEN)
    url: Optional[str] = Field(default=None, max_length=_MAX_URL_LEN)
    user_agent: Optional[str] = Field(default=None, max_length=_MAX_USER_AGENT_LEN)
    context: Optional[Dict[str, Any]] = Field(default=None)
    severity: Optional[str] = Field(default="error", max_length=20)


@router.post("/errors")
@limiter.limit("30/minute")
async def report_frontend_error(
    request: Request,
    payload: FrontendError,
    current_user=Depends(get_current_user),
):
    """Forward a frontend-captured error to GCP Error Reporting.

    Frontend's `errorReporting.js` calls this from window.onerror,
    unhandledrejection, and React ErrorBoundary.componentDidCatch. The
    backend's observability.report_exception is the same path used for
    uncaught backend exceptions, so frontend + backend errors land in
    one Error Reporting project with correlated user_id.
    """
    severity = (payload.severity or "error").lower()
    if severity not in _VALID_SEVERITIES:
        severity = "error"

    # Build a context dict that observability.report_exception will
    # serialize into the Error Reporting message + user_id field.
    extra_context = dict(payload.context or {})
    extra_context.update({
        "user_id": getattr(current_user, "user_id", None),
        "user_email": getattr(current_user, "email", None),
        "frontend_stack": payload.stack,
        "frontend_url": payload.url,
        "user_agent": payload.user_agent,
        "severity": severity,
        "source": "frontend",
    })

    # report_exception expects an exception instance. We synthesize one so
    # the SDK can attach type/message in the same shape it uses for
    # backend errors.
    exc = RuntimeError(f"frontend: {payload.message}")
    try:
        report_exception(exc, context=extra_context)
    except Exception:  # noqa: BLE001 — observability must not break the endpoint
        logger.exception("api_errors: report_exception itself failed; swallowing")

    return {"ok": True}