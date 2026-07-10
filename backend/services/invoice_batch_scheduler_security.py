"""Shared-token auth for the scheduled invoice-batch trigger.

External schedulers (Cloud Scheduler, GitHub Actions cron, n8n cron
workflows, etc.) cannot reuse the browser session JWT — they have
no user context. The canonical AEGISAXIOM pattern for "external caller
without a user session" is a per-feature shared-token env var
checked against a request header.

This module mirrors the shape of
``backend/services/procurement_webhook_security.py`` so a future
auditor sees the same fail-closed + constant-time-compare
guarantees on both surfaces.

Decisions
---------
* Env var: ``INVOICE_BATCH_SCHEDULER_TOKEN`` — chosen for parity
  with ``PROCUREMENT_BOT_WEBHOOK_SECRET`` (per-feature, descriptive,
  uppercase snake_case).
* Header: ``X-Scheduler-Token`` — short, scheduler-agnostic, no
  caller-specific naming.
* Fail-closed: when the env var is unset, the route MUST return
  503 with a structured envelope. An unconfigured deploy must
  never accept a scheduler call from any source.
* Constant-time compare: ``hmac.compare_digest`` so a malformed
  token never leaks length / prefix timing.

The token value never appears in:
  - response bodies
  - log lines
  - audit row details
  - exceptions

Cf. docs/contracts-time-mgmt/scheduler.md for the operational
runbook this module backs.
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Optional


logger = logging.getLogger(__name__)


SCHEDULER_TOKEN_HEADER = "X-Scheduler-Token"
SCHEDULER_TOKEN_ENV_VAR = "INVOICE_BATCH_SCHEDULER_TOKEN"


def is_scheduler_configured() -> bool:
    """True when the scheduler token env var is set to a non-empty
    value. Used by the route to decide whether to fail-closed at
    503 (unconfigured) or proceed to the token check. Never returns
    or echoes the value itself."""
    value = (os.environ.get(SCHEDULER_TOKEN_ENV_VAR) or "").strip()
    return bool(value)


def _resolve_token() -> Optional[str]:
    """Return the configured token if set; None otherwise. Internal
    helper. Callers must not log or echo the return value."""
    value = (os.environ.get(SCHEDULER_TOKEN_ENV_VAR) or "").strip()
    if not value:
        return None
    return value


def verify_scheduler_token(header_value: Optional[str]) -> bool:
    """Verify a presented X-Scheduler-Token header against the
    configured env token.

    Returns False when:

    * the env var is unset (the route should have returned 503
      before reaching this; the False return here is a safety
      net so an unconfigured deploy cannot accidentally accept
      any value)
    * the header is missing or empty
    * the header does not match the configured token

    Comparison uses ``hmac.compare_digest`` so the path does not
    leak timing information about how many characters matched.
    The function never logs or returns either side of the
    comparison.
    """
    expected = _resolve_token()
    if not expected:
        return False
    if not header_value:
        return False
    provided = header_value.strip()
    if not provided:
        return False
    try:
        return hmac.compare_digest(expected, provided)
    except (TypeError, ValueError):
        # compare_digest requires same-typed inputs; a malformed
        # header that breaks the contract is an invalid token, not
        # a server error.
        return False


__all__ = [
    "SCHEDULER_TOKEN_HEADER",
    "SCHEDULER_TOKEN_ENV_VAR",
    "is_scheduler_configured",
    "verify_scheduler_token",
]
