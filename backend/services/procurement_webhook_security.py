"""HMAC SHA-256 signature verification for the procurement bot webhook.

The procurement bot is a separate service. When AEGISAXIOM receives a
quote payload, the request must carry a header signed with a
shared secret so a public webhook URL cannot be exploited to
inject quote data. The helper reads the secret from env at call
time so a deploy can rotate without a server restart.

The secret value never appears in:

- response bodies
- log lines
- audit row details
- exceptions

See docs/sales-ops/PROCUREMENT_BOT_SCAFFOLD.md.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


SIGNATURE_HEADER_NAME = "X-Procurement-Bot-Signature"
SECRET_ENV_VAR = "PROCUREMENT_BOT_WEBHOOK_SECRET"


def is_webhook_configured() -> bool:
    """True when the webhook secret is present in env. Used by the
    route to return google_docs-style structured 503 envelopes when
    the deploy has not wired the bot yet. Never returns or echoes
    the value itself."""
    value = (os.environ.get(SECRET_ENV_VAR) or "").strip()
    return bool(value)


def _resolve_secret() -> Optional[str]:
    """Return the secret if configured; None otherwise. Internal
    helper. Callers must not log or echo the return value."""
    value = (os.environ.get(SECRET_ENV_VAR) or "").strip()
    if not value:
        return None
    return value


def compute_signature(body: bytes, secret: str) -> str:
    """Compute the lowercase hex HMAC-SHA256 digest of the raw
    request body using the provided secret. Pure function; no env
    read, no logging."""
    return hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()


def verify_signature(body: bytes, signature_header: Optional[str]) -> bool:
    """Verify that signature_header matches HMAC-SHA256(body, secret).

    Returns False when:

    - the secret env var is unset (the route should respond with the
      structured "not configured" envelope before calling this; the
      function returns False as a safety net so an unconfigured
      deploy cannot accidentally accept any payload)
    - the signature header is missing or empty
    - the signature header does not match the computed digest

    Comparison uses hmac.compare_digest so the path does not leak
    timing information about how many bytes matched.

    The function never logs or returns the secret or the computed
    digest. The caller must not log the provided signature header
    value either; downstream audit rows record only `has_signature:
    bool` rather than the header value.
    """
    secret = _resolve_secret()
    if not secret:
        # Secret not configured. The route should refuse before
        # reaching this branch; the False return here is a safety
        # net rather than the primary control surface.
        return False
    if not signature_header:
        return False
    expected = compute_signature(body, secret)
    # The provided header may include a scheme prefix like
    # "sha256=" depending on the bot's implementation. Tolerate
    # the prefix without leaking parser detail to the caller.
    provided = signature_header.strip()
    if provided.lower().startswith("sha256="):
        provided = provided.split("=", 1)[1]
    try:
        return hmac.compare_digest(expected, provided)
    except (TypeError, ValueError):
        # compare_digest expects equal-length string types. A
        # malformed header that breaks that contract is treated as
        # an invalid signature rather than as a server error.
        return False
