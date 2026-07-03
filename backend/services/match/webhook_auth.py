"""FastAPI dependency that gates the n8n -> MATCH inbound webhooks.

The shared bearer token lives encrypted in the match_settings singleton at
``n8n.shared_auth_token``. We look it up + decrypt it on every request — no
caching — so operators can rotate the token in the Settings UI without
restarting the backend.

Performance note: one find_one() per webhook request is fine; n8n traffic to
MATCH is low-volume (one POST per row + occasional pings). If this ever
becomes a hot path, add a short-TTL cache here rather than caching at
module load.

Auth flow:
  1. Extract Bearer from the Authorization header.
  2. Decrypt the stored ciphertext via crypto.decrypt_value().
  3. Constant-time compare to the incoming token.
  4. Raise 401 on missing/invalid.

The route module ALSO applies require_ai_permission(match.webhooks.receive)
so AI-caller authz is satisfied; the bearer check here is the real gate.
"""
from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import HTTPException, Request

from database import get_db
from services.match.crypto import decrypt_value

logger = logging.getLogger(__name__)


def _extract_bearer(request: Request) -> Optional[str]:
    """Pull the token from the Authorization header. Returns None if missing
    or the header doesn't start with 'Bearer '."""
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    if not auth.lower().startswith("bearer "):
        return None
    return auth.split(" ", 1)[1].strip()


async def require_match_webhook_bearer(request: Request) -> None:
    """FastAPI dependency. 401s if the request doesn't carry the configured
    shared bearer token.

    No-token-configured case: if the settings doc has no n8n.shared_auth_token,
    we 401. We will NOT silently allow unauthenticated webhook traffic just
    because the operator hasn't set things up yet — that would be a worse
    failure mode than a clear 401.
    """
    incoming = _extract_bearer(request)
    if not incoming:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    db = get_db()
    doc = await db.match_settings.find_one({"_singleton": True}, {"n8n": 1}) or {}
    n8n_block = doc.get("n8n") or {}
    ciphertext = n8n_block.get("shared_auth_token") or ""

    if not ciphertext:
        logger.warning("Webhook request rejected: n8n.shared_auth_token not configured")
        raise HTTPException(status_code=401, detail="Webhook auth not configured")

    try:
        expected = decrypt_value(ciphertext)
    except Exception as exc:  # InvalidToken, RuntimeError on missing key, etc.
        # Decrypt failure is operational, not authz — but we still must not
        # let the request through. Log loudly so the operator notices.
        logger.error("Failed to decrypt n8n.shared_auth_token: %s", exc)
        raise HTTPException(status_code=401, detail="Webhook auth misconfigured")

    if not expected or not hmac.compare_digest(incoming, expected):
        raise HTTPException(status_code=401, detail="Invalid bearer token")
