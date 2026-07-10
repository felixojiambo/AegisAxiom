"""Outbound HTTP client to the n8n matching workflow.

Wraps the three n8n webhook calls MATCH makes:
  - process-provider-report  — kicks off matching for a session.
  - suggest-mappings         — Magic Mapping: AI-suggested column mappings.
  - ping                     — health check (Test n8n button on Settings).

Auth: every request carries Authorization: Bearer <shared_auth_token>. The
token is decrypted from settings at CONSTRUCTION time (instances are
per-request, not long-lived) — operators can rotate the token in the
Settings UI without restarting the backend because each route handler
builds a fresh client from the current settings doc.

URLs are read from settings.n8n every time a client is constructed. No
hardcoded n8n.simplimeta.io URLs.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

from services.match.crypto import decrypt_value
from services.match.models import ProviderTemplate, Settings

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT_SECONDS = 30.0
_PING_TIMEOUT_SECONDS = 10.0


def _decrypt_token(ciphertext: str) -> str:
    """Decrypt the stored shared_auth_token. Empty in -> empty out.

    Wraps decrypt failures into HTTPException(500) so callers don't leak
    cryptography internals into route responses.
    """
    if not ciphertext:
        return ""
    try:
        return decrypt_value(ciphertext)
    except Exception as exc:
        logger.error("Failed to decrypt n8n shared_auth_token: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="n8n shared_auth_token could not be decrypted — check MATCH_FERNET_KEY",
        )


class N8nClient:
    """Async client for the three outbound n8n webhook calls.

    Constructed from a Settings instance; pulls URLs + decrypted token at
    construction. Single-use is the expected pattern — build one per request.
    """

    def __init__(self, settings: Settings) -> None:
        n8n = settings.n8n
        self._trigger_url = n8n.trigger_webhook_url
        self._magic_mapping_url = n8n.magic_mapping_webhook_url
        self._reviewer_action_url = n8n.reviewer_action_webhook_url
        # Decrypt once at construction. Stored in-memory only.
        self._auth_token = _decrypt_token(n8n.shared_auth_token)

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        if not self._auth_token:
            raise HTTPException(
                status_code=400,
                detail="n8n shared_auth_token is not configured",
            )
        return {
            "Authorization": f"Bearer {self._auth_token}",
            "Content-Type": "application/json",
        }

    async def _post_json(
        self,
        url: str,
        body: Dict[str, Any],
        *,
        timeout: float = _HTTP_TIMEOUT_SECONDS,
        op_label: str = "n8n",
    ) -> Dict[str, Any]:
        """POST JSON to an n8n webhook URL with bearer auth.

        Raises HTTPException(502) on 5xx, connection error, or timeout.
        Raises HTTPException(<status>) with the n8n response body on 4xx.
        Returns the parsed JSON dict on success.
        """
        if not url:
            raise HTTPException(
                status_code=400,
                detail=f"{op_label}: webhook URL is not configured",
            )

        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            logger.warning("%s timeout calling %s: %s", op_label, url, exc)
            raise HTTPException(status_code=502, detail=f"{op_label}: upstream timeout")
        except httpx.HTTPError as exc:
            logger.warning("%s connection error calling %s: %s", op_label, url, exc)
            raise HTTPException(
                status_code=502, detail=f"{op_label}: upstream connection error"
            )

        if 500 <= resp.status_code < 600:
            logger.warning(
                "%s upstream %s: %s", op_label, resp.status_code, resp.text[:500]
            )
            raise HTTPException(
                status_code=502,
                detail=f"{op_label}: upstream returned {resp.status_code}",
            )

        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:500]
            raise HTTPException(status_code=resp.status_code, detail=detail)

        try:
            return resp.json()
        except Exception:
            # n8n nodes sometimes return empty bodies on success — treat as {}.
            return {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_provider_report(
        self,
        *,
        session_id: str,
        provider_report_csv: str,
        snapshot_customers_csv: str,
        template: ProviderTemplate,
        callback_url: str,
        provider: str = "nextiva",
        snapshot_orders: str = "",
        template_id: Optional[str] = None,
        google_sheet_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Kick off the n8n matching workflow for a session.

        n8n will POST one /webhooks/row-processed callback per row back to
        ``callback_url`` once matching completes.
        """
        # Wire adapter — AEGISAXIOM uses data-dictionary-aligned field names
        # internally; n8n's nodes still expect the POC source-of-truth shape.
        # We translate on the wire so AEGISAXIOM keeps its conventions and n8n's
        # validators see the keys they were built for.
        #
        # Translations applied:
        #   template.template_id              -> template.id          (validate-payload node)
        #   column_mappings[].source_column   -> provider_column      (build-column-lookup node)
        #   column_mappings[].target_path     -> appdirect_field      (build-column-lookup node)
        #   column_mappings[].transforms      -> transform (singular) (apply-transforms node)
        #
        # `transforms` is a list of named transforms in AEGISAXIOM; n8n's node
        # reads a single `transform` value. We take the first entry —
        # none of the canonical templates chain transforms today.
        template_dict = template.model_dump()
        template_dict["id"] = template_id or template_dict.get("template_id")

        translated_mappings = []
        for m in template_dict.get("column_mappings") or []:
            transforms = m.get("transforms") or []
            translated_mappings.append({
                "provider_column": m.get("source_column"),
                "appdirect_field": m.get("target_path"),
                "transform": transforms[0] if transforms else None,
                "required": bool(m.get("required")),
                "appdirect_label": m.get("appdirect_label"),
                "notes": m.get("notes"),
            })
        template_dict["column_mappings"] = translated_mappings

        body: Dict[str, Any] = {
            "mode": "process",  # n8n Validate Mode node requires this; "ping" is the other accepted value.
            "session_id": session_id,
            "callback_url": callback_url,
            "provider_report": provider_report_csv,
            "snapshot_customers": snapshot_customers_csv,
            "template": template_dict,
            "provider": provider,
            "snapshot_orders": snapshot_orders,
            "template_id": template_id,
            "google_sheet_url": google_sheet_url,
        }
        return await self._post_json(
            self._trigger_url, body, op_label="n8n process-provider-report"
        )

    async def suggest_mappings(
        self,
        *,
        provider_report_url: str,
        snapshot_customers_url: str,
        template_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ask n8n for AI-suggested column mappings between the provider report
        and the snapshot customers CSV. Used by the Magic Mapping button on
        the provider-template edit page."""
        body: Dict[str, Any] = {
            "provider_report_url": provider_report_url,
            "snapshot_customers_url": snapshot_customers_url,
            "template_id": template_id,
        }
        return await self._post_json(
            self._magic_mapping_url, body, op_label="n8n suggest-mappings"
        )

    async def ping(self) -> Dict[str, Any]:
        """Health-check the configured trigger webhook by POSTing
        {"mode": "ping"}.

        The n8n workflow's Mode Switch returns a Pong response when the
        body's ``mode`` key equals "ping", on the same trigger URL.

        Returns a flat dict {ok, status_code, latency_ms, hint?} that the
        Settings UI displays inline. Does NOT raise on upstream errors —
        the whole point is to give a friendly diagnostic.
        """
        url = self._trigger_url
        if not url:
            return {
                "ok": False,
                "status_code": 0,
                "latency_ms": 0,
                "hint": "Configure the trigger webhook URL on the Settings tab first.",
            }
        if not self._auth_token:
            return {
                "ok": False,
                "status_code": 0,
                "latency_ms": 0,
                "hint": "Configure the n8n shared auth token on the Settings tab first.",
            }

        headers = {
            "Authorization": f"Bearer {self._auth_token}",
            "Content-Type": "application/json",
        }
        body = {"mode": "ping", "app": "match"}
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_PING_TIMEOUT_SECONDS) as client:
                resp = await client.post(url, json=body, headers=headers)
        except Exception as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            return {
                "ok": False,
                "status_code": 0,
                "latency_ms": latency_ms,
                "hint": f"Could not reach n8n: {exc}",
            }

        latency_ms = int((time.monotonic() - started) * 1000)
        status = resp.status_code

        hint: Optional[str] = None
        if status == 403:
            hint = (
                "n8n returned 403 — auth header didn't match the credential on n8n's "
                "side. Common causes: (1) the n8n Header Auth credential's Value should "
                "be 'Bearer <token>' with the 'Bearer ' prefix; (2) the workflow isn't "
                "active — Production webhook URLs only respond when the workflow is "
                "switched on; (3) trailing whitespace or 'Bearer ' duplicated in the "
                "shared auth token."
            )
        elif status == 404:
            hint = (
                "n8n returned 404 — the webhook path doesn't exist. Make sure the "
                "workflow is activated and the path matches the trigger node."
            )
        elif status == 401:
            hint = (
                "n8n returned 401 — token mismatch. Re-paste the raw token (no 'Bearer '"
                " prefix) and try again."
            )

        return {
            "ok": status < 300,
            "status_code": status,
            "latency_ms": latency_ms,
            "hint": hint,
        }
