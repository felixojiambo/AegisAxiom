"""AEGISAXIOM-side client for the future procurement bot service.

The procurement bot is a separate service that AEGISAXIOM submits BOM
payloads to. The bot fetches quotes from vendor portals (out of
AEGISAXIOM scope) and posts quote results back via the signed webhook
in routes/sales_ops_procurement.py.

This client only sends the BOM. It does not:

- query vendor portals directly
- store vendor credentials
- contain vendor names or rankings
- decide which vendor to pick

Per Ticket 8 and Ticket 9 spec, AEGISAXIOM runs in Mode B
(manual-review) when the bot is not configured. The client
returns a typed result so the route can route the response into
the manual-review queue without leaking exception text or
credentials.

Env config (placeholders only, never printed):

  PROCUREMENT_BOT_BASE_URL
  PROCUREMENT_BOT_API_KEY
  PROCUREMENT_BOT_TIMEOUT_SECONDS  (optional, default 30)

See docs/sales-ops/PROCUREMENT_BOT_SCAFFOLD.md.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


BOT_BASE_URL_ENV = "PROCUREMENT_BOT_BASE_URL"
BOT_API_KEY_ENV = "PROCUREMENT_BOT_API_KEY"
BOT_TIMEOUT_ENV = "PROCUREMENT_BOT_TIMEOUT_SECONDS"
DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass
class BotSubmissionResult:
    """Outcome of a procurement bot submission.

    configured tells the route whether the bot service is wired in
    this deploy. accepted tells the route whether the bot accepted
    the submission for processing. bot_request_id is the bot's
    handle for the in-flight job (the bot will return it in the
    webhook callback). error_reason is a short, safe string the
    route can include in an audit row; raw exception text and
    response bodies never reach the audit detail field.
    """

    configured: bool
    accepted: bool
    bot_request_id: Optional[str] = None
    error_reason: Optional[str] = None


def is_bot_configured() -> bool:
    """True when both the base URL and the API key are set. Used by
    the route to decide whether to attempt submission or land
    directly in the manual-review queue. Never returns the values
    themselves."""
    base = (os.environ.get(BOT_BASE_URL_ENV) or "").strip()
    key = (os.environ.get(BOT_API_KEY_ENV) or "").strip()
    return bool(base and key)


def _resolve_timeout() -> float:
    raw = (os.environ.get(BOT_TIMEOUT_ENV) or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        v = float(raw)
        if v <= 0:
            return DEFAULT_TIMEOUT_SECONDS
        return v
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


def build_submission_payload(
    *,
    sourcing_request_id: str,
    opportunity_id: str,
    line_items: list[dict],
    sow_id: Optional[str] = None,
    callback_identifier: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble the BOM payload the bot expects.

    Only structured material data crosses the wire. Free-text
    fields the bot does not need (audit notes, internal IDs,
    customer-facing wording) are intentionally omitted. The
    callback_identifier is what the bot echoes back in the webhook
    so AEGISAXIOM can reconcile.

    No tenant_id is sent in the payload. Tenant scoping is
    resolved AEGISAXIOM-side on the webhook callback through the stored
    sourcing_request_id; the bot does not need to know which
    tenant a request belongs to.
    """
    return {
        "sourcing_request_id": sourcing_request_id,
        "opportunity_id": opportunity_id,
        "sow_id": sow_id,
        "callback_identifier": callback_identifier or sourcing_request_id,
        "lines": [
            {
                "material_line_id": li.get("line_item_id"),
                "category": li.get("category"),
                "subcategory": li.get("subcategory"),
                "item_name": li.get("item_name"),
                "quantity": li.get("quantity"),
                "unit": li.get("unit"),
                "supplier_sku": li.get("supplier_sku"),
            }
            for li in line_items or []
        ],
    }


async def submit_sourcing_request(
    payload: dict[str, Any],
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> BotSubmissionResult:
    """Send the BOM payload to the configured procurement bot.

    Returns a BotSubmissionResult regardless of outcome. The
    function never raises an exception on a network or HTTP error;
    every failure path collapses into result.accepted=False with a
    safe error_reason. The route decides whether to record the
    failure in the audit row and whether to keep the sourcing
    request in submitted, review_required, or failed status.

    The Authorization header is set from PROCUREMENT_BOT_API_KEY.
    The header value never reaches the response, the result, the
    log line, or any audit row.
    """
    if not is_bot_configured():
        return BotSubmissionResult(
            configured=False,
            accepted=False,
            error_reason="bot_not_configured",
        )

    # Resolve env at call time using .get with a strip so a race
    # condition between is_bot_configured and the actual read
    # collapses to bot_not_configured rather than a KeyError. The
    # function contract is "never raises"; the explicit reads
    # protect that contract.
    base_url_raw = (os.environ.get(BOT_BASE_URL_ENV) or "").strip()
    api_key = (os.environ.get(BOT_API_KEY_ENV) or "").strip()
    if not base_url_raw or not api_key:
        return BotSubmissionResult(
            configured=False,
            accepted=False,
            error_reason="bot_not_configured",
        )
    base_url = base_url_raw.rstrip("/")
    timeout = _resolve_timeout()

    # The endpoint path is intentionally not configurable through
    # env so a deploy cannot accidentally point at a non-bot URL.
    # If the future bot uses a different path the constant moves
    # under explicit code review.
    url = f"{base_url}/v1/sourcing-requests"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        try:
            response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as e:
            logger.warning(
                "Procurement bot submission failed at transport layer: %s",
                e,
            )
            return BotSubmissionResult(
                configured=True,
                accepted=False,
                error_reason="transport_error",
            )
        except Exception as e:  # noqa: BLE001
            # Defensive: the function contract is "never raises".
            # Any unexpected exception (json serialization, etc) is
            # treated as a transport error so the route can land in
            # the manual-review queue.
            logger.warning(
                "Procurement bot submission failed with unexpected error: %s",
                e,
            )
            return BotSubmissionResult(
                configured=True,
                accepted=False,
                error_reason="transport_error",
            )

        if response.status_code >= 500:
            logger.warning(
                "Procurement bot returned %s on submission",
                response.status_code,
            )
            return BotSubmissionResult(
                configured=True,
                accepted=False,
                error_reason="bot_5xx",
            )
        if response.status_code >= 400:
            logger.warning(
                "Procurement bot rejected submission with status %s",
                response.status_code,
            )
            return BotSubmissionResult(
                configured=True,
                accepted=False,
                error_reason="bot_4xx",
            )

        try:
            body = response.json()
        except ValueError:
            body = {}
        bot_request_id = None
        if isinstance(body, dict):
            value = body.get("bot_request_id")
            if isinstance(value, str) and value:
                bot_request_id = value

        return BotSubmissionResult(
            configured=True,
            accepted=True,
            bot_request_id=bot_request_id,
        )
    finally:
        if owns_client:
            await client.aclose()
