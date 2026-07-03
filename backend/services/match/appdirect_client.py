"""AppDirect Provider Sales API client.

Wraps the outbound HTTP calls MATCH makes to AppDirect when pushing approved
orders. Every call attempt — live or dry-run, success or failure — writes a
doc to ``match_appdirect_calls``. The bearer token NEVER appears in the log:
the Authorization header is recorded as MASKED_PASSWORD_SENTINEL.

Constructed from a Settings instance + db handle + actor id; pulls the
decrypted bearer at construction. Per-request lifecycle — operators can
rotate the token in Settings without restarting the backend because each
route handler builds a fresh client from the current settings doc.

Safety switch: if settings.appdirect.live_post_enabled is False, every
push_order call is forced to dry_run mode regardless of the caller's
``dry_run`` flag value.

Lookup-tables endpoints (used by Settings -> Sync Lookup Tables) come from
the MATCH source list at routers/settings.py::_LOOKUP_ENDPOINTS:
  products, providers, contract-types, contract-terms, order-statuses,
  advisors, users.

----------------------------------------------------------------------------
Push chain (Wave 6 — Thread A)
----------------------------------------------------------------------------
``push_order`` decomposes a single approved row into the real AppDirect
Provider Sales API sequence:

    1. (optional) POST /api/services/app/Customers/v2/create
       — skipped when the row already has matched_candidate_id set.
    2.  GET  /api/services/app/Customers/v2/{customerId}/locations
       — to find an existing location by normalized address.
    3.  POST /api/services/app/Customers/v2/{customerId}/locations
       — only when (2) returns no match.
    4.  POST /api/v2/orders
    5.  POST /api/v2/orderDetails

Every step writes its own log doc to ``match_appdirect_calls`` so the row
drawer's Push History sub-section can render the full sequence. The chain
HALTS on the first non-2xx response or connection error; subsequent steps
are not attempted.

``push_order`` returns the **terminal** AppDirectCall (the OrderDetail
create — or whichever step failed and stopped the chain). Intermediate
calls are accessible via the call log in Mongo, keyed by row_id.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from models import gen_id
from services.match.crypto import MASKED_PASSWORD_SENTINEL, decrypt_value
from services.match.models import AppDirectCall, Settings

logger = logging.getLogger(__name__)

# Endpoints MATCH attempts to sync from AppDirect for lookup tables. Order
# matters only for the response dict. Mirrors routers/settings.py in source.
_LOOKUP_ENDPOINTS: Dict[str, str] = {
    "products": "/api/v2/products",
    "providers": "/api/v2/providers",
    "contractTypes": "/api/v2/contract-types",
    "contractTerms": "/api/v2/contract-terms",
    "orderStatuses": "/api/v2/order-statuses",
    "advisors": "/api/v2/advisors",
    "users": "/api/v2/users",
}

# Default timeout for AppDirect calls. The order-create endpoint takes the
# longest in practice; 30s matches the MATCH source.
_HTTP_TIMEOUT_SECONDS = 30.0
_LOOKUP_TIMEOUT_SECONDS = 15.0

# Statuses that the push chain will accept. Anything else gets a "not
# pushable" error from push_order without making any HTTP calls.
_PUSHABLE_STATUSES = {"approved", "edited", "create_new", "create_new_order"}

# Regex used by _normalize_address — strips punctuation + collapses whitespace
# so "123 Main St." matches "123 Main St" matches "123  Main  St".
_PUNCT_RE = re.compile(r"[^\w\s]")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redacted_headers() -> Dict[str, str]:
    """Headers dict safe for persistence in match_appdirect_calls."""
    return {
        "Authorization": MASKED_PASSWORD_SENTINEL,
        "Content-Type": "application/json",
    }


def _normalize_address(*parts: Optional[str]) -> str:
    """Normalize an address for fuzzy comparison.

    Lowercases, strips punctuation, collapses whitespace. Mirrors the
    helper in the MATCH source so the same string from a different code
    path (e.g. a previously-created location vs. a freshly-built payload)
    compares equal.
    """
    joined = " ".join((p or "").strip() for p in parts if p is not None).lower()
    joined = _PUNCT_RE.sub(" ", joined)
    return " ".join(joined.split())


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Parse a Contract H numeric value (handles "$1,234.50", "USD 12.00")."""
    if val is None or val == "":
        return default
    try:
        return float(str(val).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return default


def _fuzzy_lookup(text: Optional[str], items: List[Dict[str, Any]], name_key: str = "name") -> Optional[int]:
    """Match text against a list of {id, name} lookup items.

    Tries exact (case-insensitive) match, then substring, then a
    digits-extracted match for terms like "36 Months". Returns the int id
    or None.
    """
    if not text or not items:
        return None
    normalized = text.strip().lower()
    for it in items:
        if (it.get(name_key) or "").strip().lower() == normalized:
            try:
                return int(it.get("id") or 0) or None
            except (TypeError, ValueError):
                return None
    digits = "".join(c for c in text if c.isdigit())
    if digits:
        for it in items:
            iname = (it.get(name_key) or "").strip().lower()
            if digits in iname or iname.startswith(digits):
                try:
                    return int(it.get("id") or 0) or None
                except (TypeError, ValueError):
                    continue
            if str(it.get("monthsCount") or "") == digits:
                try:
                    return int(it.get("id") or 0) or None
                except (TypeError, ValueError):
                    continue
    return None


class AppDirectClient:
    """Async client for the AppDirect Provider Sales API.

    Every method logs a doc to ``match_appdirect_calls`` (regardless of
    dry_run / live / success / failure) so the row drawer Push History
    sub-section can render attempts uniformly.
    """

    def __init__(self, settings: Settings, *, db: Any, actor: str) -> None:
        ad = settings.appdirect
        self._base_url = (ad.base_url or "").rstrip("/")
        self._multipass_enabled = ad.multipass_enabled
        self._live_post_enabled = ad.live_post_enabled
        self._defaults = ad.defaults
        self._cached_lookups: Dict[str, Any] = ad.cached_lookups or {}
        self._db = db
        self._actor = actor

        # Decrypt the bearer once at construction. In-memory only.
        ciphertext = ad.bearer_token or ""
        if ciphertext:
            try:
                self._bearer_token = decrypt_value(ciphertext)
            except Exception as exc:
                logger.error("Failed to decrypt AppDirect bearer_token: %s", exc)
                self._bearer_token = ""
        else:
            self._bearer_token = ""

    # ------------------------------------------------------------------
    # Internal helpers — logging
    # ------------------------------------------------------------------

    def _auth_headers(self) -> Dict[str, str]:
        """Real headers used for live httpx calls. Never persisted."""
        return {
            "Authorization": f"Bearer {self._bearer_token}",
            "Content-Type": "application/json",
        }

    async def _log_call(self, call: AppDirectCall) -> None:
        """Insert one call log doc. ``call.request_headers_redacted`` MUST
        already use the sentinel — this method does not transform it."""
        doc = call.model_dump()
        # Defensive: if a caller ever slipped a real Authorization through,
        # overwrite it here so the plaintext bearer cannot land in storage.
        headers = doc.get("request_headers_redacted") or {}
        if "Authorization" in headers and headers["Authorization"] != MASKED_PASSWORD_SENTINEL:
            headers["Authorization"] = MASKED_PASSWORD_SENTINEL
        if "authorization" in headers:
            headers["authorization"] = MASKED_PASSWORD_SENTINEL
        doc["request_headers_redacted"] = headers
        await self._db.match_appdirect_calls.insert_one(doc)

    async def _log_dry_run(
        self,
        *,
        endpoint: str,
        request_body: Dict[str, Any],
        row_id: str,
        session_id: str,
        error: Optional[str] = None,
    ) -> AppDirectCall:
        """Persist a dry-run log row and return the AppDirectCall."""
        call = AppDirectCall(
            call_id=gen_id("m_adc_"),
            row_id=row_id,
            session_id=session_id,
            endpoint=endpoint,
            request_body=request_body,
            request_headers_redacted=_redacted_headers(),
            response_status=None,
            response_body=None,
            latency_ms=None,
            error=error,
            mode="dry_run",
            actor=self._actor,
            created_at=_now_iso(),
        )
        await self._log_call(call)
        return call

    async def _log_live(
        self,
        *,
        endpoint: str,
        request_body: Optional[Dict[str, Any]],
        row_id: str,
        session_id: str,
        response_status: Optional[int],
        response_body: Optional[Any],
        latency_ms: Optional[int],
        error: Optional[str],
    ) -> AppDirectCall:
        """Persist a live-call log row and return the AppDirectCall."""
        call = AppDirectCall(
            call_id=gen_id("m_adc_"),
            row_id=row_id,
            session_id=session_id,
            endpoint=endpoint,
            request_body=request_body or {},
            request_headers_redacted=_redacted_headers(),
            response_status=response_status,
            response_body=response_body,
            latency_ms=latency_ms,
            error=error,
            mode="live",
            actor=self._actor,
            created_at=_now_iso(),
        )
        await self._log_call(call)
        return call

    async def _live_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        row_id: str = "",
        session_id: str = "",
        timeout: float = _HTTP_TIMEOUT_SECONDS,
    ) -> AppDirectCall:
        """Make a real HTTP call to AppDirect and log the attempt.

        Returns the AppDirectCall (including parsed response_body, status,
        latency, and error). Does NOT raise on non-2xx — the caller inspects
        the AppDirectCall.response_status and decides.
        """
        # Fail loudly on missing config — the previous behavior of logging a
        # synthetic "live" call and returning it silently caused the Settings
        # "Test AppDirect" button to report success against an empty form.
        # The test-* endpoints + push handler all wrap calls in try/except, so
        # raising here surfaces a real error to the caller.
        if not self._base_url:
            raise HTTPException(
                status_code=400,
                detail="AppDirect base_url not configured. Set Settings -> AppDirect -> API Base URL and Save.",
            )
        if not self._bearer_token:
            raise HTTPException(
                status_code=400,
                detail="AppDirect bearer_token not configured. Set Settings -> AppDirect -> Bearer Token and Save.",
            )

        url = f"{self._base_url}{path}"
        endpoint_label = f"{method.upper()} {path}"
        started = time.monotonic()
        response_status: Optional[int] = None
        response_body: Optional[Any] = None
        error_str: Optional[str] = None

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(
                    method=method.upper(),
                    url=url,
                    json=json_body,
                    headers=self._auth_headers(),
                )
            response_status = resp.status_code
            try:
                response_body = resp.json()
            except Exception:
                response_body = resp.text[:2000] if resp.text else None
        except Exception as exc:
            error_str = str(exc)[:500]

        latency_ms = int((time.monotonic() - started) * 1000)
        return await self._log_live(
            endpoint=endpoint_label,
            request_body=json_body,
            row_id=row_id,
            session_id=session_id,
            response_status=response_status,
            response_body=response_body,
            latency_ms=latency_ms,
            error=error_str,
        )

    # ------------------------------------------------------------------
    # FK resolvers — consume settings.appdirect.cached_lookups + defaults.
    # No DB reads here — the lookup cache was hydrated into self at
    # construction time.
    # ------------------------------------------------------------------

    def _lookups(self, key: str) -> List[Dict[str, Any]]:
        items = self._cached_lookups.get(key) if isinstance(self._cached_lookups, dict) else None
        return items if isinstance(items, list) else []

    def _resolve_advisor_id(self, template: Dict[str, Any]) -> int:
        tid = template.get("appdirect_advisor_id")
        if tid:
            try:
                if int(tid) > 0:
                    return int(tid)
            except (TypeError, ValueError):
                pass
        advisors = self._lookups("advisors")
        if advisors:
            try:
                return int(advisors[0].get("id") or 0)
            except (TypeError, ValueError):
                return 0
        return 0

    def _resolve_product_id(
        self,
        product_text: Optional[str],
        provider_id_hint: int,
        template: Dict[str, Any],
    ) -> int:
        products = self._lookups("products")
        if product_text and products:
            normalized = product_text.strip().lower()
            exact_with_provider: Optional[int] = None
            exact_any: Optional[int] = None
            for it in products:
                if (it.get("name") or "").strip().lower() == normalized:
                    try:
                        pid = int(it.get("id") or 0)
                    except (TypeError, ValueError):
                        continue
                    if provider_id_hint and int(it.get("providerId") or 0) == provider_id_hint:
                        exact_with_provider = pid
                        break
                    if exact_any is None:
                        exact_any = pid
            matched = exact_with_provider or exact_any
            if matched is None:
                # Substring fallback.
                for it in products:
                    iname = (it.get("name") or "").strip().lower()
                    if iname and (normalized in iname or iname in normalized):
                        try:
                            matched = int(it.get("id") or 0)
                        except (TypeError, ValueError):
                            continue
                        break
            if matched:
                return matched
        try:
            return int(template.get("appdirect_product_id") or 0)
        except (TypeError, ValueError):
            return 0

    def _resolve_provider_id(self, provider_text: Optional[str], template: Dict[str, Any]) -> int:
        providers = self._lookups("providers")
        if provider_text and providers:
            normalized = provider_text.strip().lower()
            for it in providers:
                if (it.get("name") or "").strip().lower() == normalized:
                    try:
                        return int(it.get("id") or 0)
                    except (TypeError, ValueError):
                        continue
            for it in providers:
                iname = (it.get("name") or "").strip().lower()
                if iname and (normalized in iname or iname in normalized):
                    try:
                        return int(it.get("id") or 0)
                    except (TypeError, ValueError):
                        continue
        try:
            return int(template.get("appdirect_provider_id") or 0)
        except (TypeError, ValueError):
            return 0

    def _resolve_contract_type_id(self, type_text: Optional[str]) -> int:
        types = self._lookups("contractTypes")
        match = _fuzzy_lookup(type_text, types)
        if match:
            return match
        # Fall back to the configured "new" default.
        return int(self._defaults.contract_type_id_new or 0)

    def _resolve_contract_term_id(self, term_text: Optional[str]) -> int:
        terms = self._lookups("contractTerms")
        match = _fuzzy_lookup(term_text, terms)
        if match:
            return match
        # Map common terms onto the configured defaults.
        digits = "".join(c for c in (term_text or "") if c.isdigit())
        if digits == "12":
            return int(self._defaults.contract_term_id_12mo or 0)
        if digits == "24":
            return int(self._defaults.contract_term_id_24mo or 0)
        if digits == "36":
            return int(self._defaults.contract_term_id_36mo or 0)
        # Last-ditch: any configured term default.
        for fallback in (
            self._defaults.contract_term_id_12mo,
            self._defaults.contract_term_id_24mo,
            self._defaults.contract_term_id_36mo,
        ):
            if fallback:
                return int(fallback)
        return 0

    def _resolve_sales_agent_id(self, row: Dict[str, Any]) -> int:
        # 1. Defaults override always wins when set.
        override = int(self._defaults.sales_agent_id or 0)
        if override:
            return override
        # 2. row.output.sales_rep / output.top_level.sales_rep email match.
        output = row.get("output") or {}
        top_level = output.get("top_level") or {}
        sales_rep = (
            (top_level.get("sales_rep") or output.get("sales_rep") or "").strip().lower()
        )
        users = self._lookups("users")
        if sales_rep and users:
            for u in users:
                if (u.get("email") or "").strip().lower() == sales_rep:
                    try:
                        return int(u.get("id") or 0)
                    except (TypeError, ValueError):
                        continue
        # 3. Actor email match.
        if self._actor and users:
            actor_lc = self._actor.strip().lower()
            for u in users:
                if (u.get("email") or "").strip().lower() == actor_lc:
                    try:
                        return int(u.get("id") or 0)
                    except (TypeError, ValueError):
                        continue
        # 4. Refuse — but soft-refuse for dry-run callers by returning 0.
        # The live HTTP call to AppDirect will fail clearly if 0 is sent,
        # which is the same posture the source POC took.
        return 0

    def _resolve_account_manager_id(self, template: Dict[str, Any], row: Dict[str, Any]) -> int:
        tpl = template.get("appdirect_account_manager_id")
        if tpl:
            try:
                if int(tpl) > 0:
                    return int(tpl)
            except (TypeError, ValueError):
                pass
        output = row.get("output") or {}
        top_level = output.get("top_level") or {}
        customer = top_level.get("customer") or {}
        am_email = (customer.get("account_manager_email") or "").strip().lower()
        users = self._lookups("users")
        if am_email and users:
            for u in users:
                if (u.get("email") or "").strip().lower() == am_email:
                    try:
                        return int(u.get("id") or 0)
                    except (TypeError, ValueError):
                        continue
        return 0

    # ------------------------------------------------------------------
    # Payload builders — Contract H output.* -> AppDirect request bodies
    # ------------------------------------------------------------------

    def _build_customer_payload(
        self, row: Dict[str, Any], template: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Body for POST /api/services/app/Customers/v2/create.

        Required (Swagger): name, address, city, state, zipCode, advisorId.
        """
        output = row.get("output") or {}
        top_level = output.get("top_level") or {}
        customer = top_level.get("customer") or {}
        location = top_level.get("location") or {}
        payload: Dict[str, Any] = {
            "name": customer.get("name") or "",
            "address": location.get("street") or location.get("address") or "",
            "city": location.get("city") or "",
            "state": location.get("state") or "",
            "zipCode": location.get("zip") or location.get("zipcode") or "",
            "advisorId": self._resolve_advisor_id(template),
            # We always create the named location explicitly in step 2/3
            # so AppDirect does not auto-create a duplicate.
            "createDefaultLocation": False,
        }
        amid = self._resolve_account_manager_id(template, row)
        if amid:
            payload["accountManagerId"] = amid
        return payload

    def _build_customer_location_payload(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Body for POST /api/services/app/Customers/v2/{customerId}/locations.

        Required (Swagger): locationName, address, zipcode, city, state — wrapped
        in a ``{locations: [...]}`` envelope.
        """
        output = row.get("output") or {}
        top_level = output.get("top_level") or {}
        customer = top_level.get("customer") or {}
        location = top_level.get("location") or {}
        loc_name = location.get("name") or customer.get("name") or "Primary Location"
        return {
            "locations": [
                {
                    "locationName": loc_name,
                    "address": location.get("street") or location.get("address") or "",
                    "zipcode": location.get("zip") or location.get("zipcode") or "",
                    "city": location.get("city") or "",
                    "state": location.get("state") or "",
                }
            ]
        }

    def _build_order_payload(
        self, row: Dict[str, Any], customer_id: int
    ) -> Dict[str, Any]:
        """Body for POST /api/v2/orders.

        Required (Swagger): customerId, salesAgentId. We also forward the
        ``providerOrderId`` from Contract H so AppDirect can correlate
        with the provider's internal order ID — the row maps it back to
        appdirect_order_id on success.
        """
        payload: Dict[str, Any] = {
            "customerId": customer_id,
            "salesAgentId": self._resolve_sales_agent_id(row),
        }
        output = row.get("output") or {}
        order_details = output.get("order_details") or {}
        provider_order_id = (
            order_details.get("provider_sales_order_id")
            or order_details.get("provider_order_id")
        )
        if provider_order_id:
            # OpenAPI spec calls this providerOrderId on the OrderDetail
            # request — the source POC also forwards it on Order.create for
            # AppDirect-side correlation. Harmless duplicate.
            payload["providerOrderId"] = str(provider_order_id)
        return payload

    def _build_order_detail_payload(
        self,
        row: Dict[str, Any],
        template: Dict[str, Any],
        *,
        order_id_or_number: str,
        customer_location_ids: List[int],
    ) -> Dict[str, Any]:
        """Body for POST /api/v2/orderDetails.

        Required (Swagger): idOrOrderNumber, customerLocationIds, productId,
        providerId, contract.
        """
        output = row.get("output") or {}
        order_details = output.get("order_details") or {}
        order_attributes = output.get("order_attributes") or {}

        provider_text = (
            order_details.get("provider_name")
            or order_details.get("provider")
        )
        provider_id = self._resolve_provider_id(provider_text, template)
        product_text = (
            order_details.get("product_name")
            or order_details.get("product")
        )
        product_id = self._resolve_product_id(product_text, provider_id, template)

        contract: Dict[str, Any] = {
            "contractTypeId": self._resolve_contract_type_id(order_details.get("contract_type")),
            "contractTermId": self._resolve_contract_term_id(order_details.get("contract_term")),
            "mrgmv": _safe_float(order_details.get("mrgmv") or order_details.get("mrc")),
            "nrc": _safe_float(order_details.get("nrc")),
            "contractSignedDate": (
                order_details.get("contract_signed")
                or order_details.get("contract_signed_date")
                or ""
            ),
        }
        if order_details.get("contract_start"):
            contract["contractStartDate"] = order_details["contract_start"]
        if order_details.get("contract_end"):
            contract["contractEndDate"] = order_details["contract_end"]
        if order_details.get("net_new_revenue"):
            contract["netNewRevenue"] = _safe_float(order_details["net_new_revenue"])

        payload: Dict[str, Any] = {
            "idOrOrderNumber": str(order_id_or_number),
            "customerLocationIds": list(customer_location_ids),
            "productId": product_id,
            "providerId": provider_id,
            "contract": contract,
        }

        # commissionBillingAccountNumber is obsolete when multipass is on.
        if not self._multipass_enabled:
            ban = order_details.get("commission_ban")
            if ban:
                payload["commissionBillingAccountNumber"] = str(ban)

        # Optional pass-throughs from Contract H.
        provider_order_id = (
            order_details.get("provider_order_id")
            or order_details.get("provider_sales_order_id")
        )
        if provider_order_id:
            payload["providerOrderId"] = str(provider_order_id)
        advisor_order_id = (
            order_attributes.get("advisor_order_id")
            or order_details.get("order_number")
        )
        if advisor_order_id:
            payload["advisorOrderId"] = str(advisor_order_id)
        return payload

    # ------------------------------------------------------------------
    # Step methods — each makes one HTTP call (or dry-run log) and returns
    # the AppDirectCall + the "useful piece" of the response body. The
    # orchestrator threads these together.
    # ------------------------------------------------------------------

    async def _step_create_customer(
        self,
        row: Dict[str, Any],
        template: Dict[str, Any],
        *,
        dry_run: bool,
    ) -> Tuple[AppDirectCall, Optional[int]]:
        payload = self._build_customer_payload(row, template)
        path = "/api/services/app/Customers/v2/create"
        row_id = row.get("row_id") or ""
        session_id = row.get("session_id") or ""
        if dry_run:
            call = await self._log_dry_run(
                endpoint=f"POST {path}",
                request_body=payload,
                row_id=row_id,
                session_id=session_id,
            )
            return call, None
        call = await self._live_request(
            "POST", path, json_body=payload, row_id=row_id, session_id=session_id
        )
        customer_id = _extract_id(call.response_body)
        return call, customer_id

    async def _step_find_location(
        self,
        row: Dict[str, Any],
        customer_id: int,
        *,
        dry_run: bool,
    ) -> Tuple[AppDirectCall, Optional[int]]:
        """GET locations and try to match by normalized address. Returns the
        matched location id or None."""
        path = f"/api/services/app/Customers/v2/{customer_id}/locations"
        row_id = row.get("row_id") or ""
        session_id = row.get("session_id") or ""
        if dry_run:
            call = await self._log_dry_run(
                endpoint=f"GET {path}",
                request_body={"customerId": customer_id},
                row_id=row_id,
                session_id=session_id,
            )
            return call, None
        call = await self._live_request(
            "GET", path, json_body=None, row_id=row_id, session_id=session_id
        )
        if call.response_status is None or not (200 <= call.response_status < 300):
            return call, None
        # Normalize the row's location and look for a match in the response.
        output = row.get("output") or {}
        loc = (output.get("top_level") or {}).get("location") or {}
        want = _normalize_address(
            loc.get("street") or loc.get("address"),
            loc.get("city"),
            loc.get("state"),
            loc.get("zip") or loc.get("zipcode"),
        )
        items = call.response_body
        if isinstance(items, dict):
            items = items.get("items") or items.get("data") or []
        if not isinstance(items, list):
            return call, None
        for it in items:
            if not isinstance(it, dict):
                continue
            have = _normalize_address(
                it.get("address"),
                it.get("city"),
                it.get("state"),
                it.get("zipcode") or it.get("zipCode"),
            )
            if have and have == want:
                try:
                    return call, int(it.get("id") or 0) or None
                except (TypeError, ValueError):
                    return call, None
        return call, None

    async def _step_create_location(
        self,
        row: Dict[str, Any],
        customer_id: int,
        *,
        dry_run: bool,
    ) -> Tuple[AppDirectCall, Optional[int]]:
        payload = self._build_customer_location_payload(row)
        path = f"/api/services/app/Customers/v2/{customer_id}/locations"
        row_id = row.get("row_id") or ""
        session_id = row.get("session_id") or ""
        if dry_run:
            call = await self._log_dry_run(
                endpoint=f"POST {path}",
                request_body=payload,
                row_id=row_id,
                session_id=session_id,
            )
            return call, None
        call = await self._live_request(
            "POST", path, json_body=payload, row_id=row_id, session_id=session_id
        )
        # Response is a JSON array per Swagger — take first id.
        body = call.response_body
        if isinstance(body, list) and body and isinstance(body[0], dict):
            try:
                return call, int(body[0].get("id") or 0) or None
            except (TypeError, ValueError):
                return call, None
        if isinstance(body, dict):
            arr = body.get("data") or body.get("items")
            if isinstance(arr, list) and arr and isinstance(arr[0], dict):
                try:
                    return call, int(arr[0].get("id") or 0) or None
                except (TypeError, ValueError):
                    return call, None
            if body.get("id"):
                try:
                    return call, int(body["id"]) or None
                except (TypeError, ValueError):
                    return call, None
        return call, None

    async def _step_create_order(
        self,
        row: Dict[str, Any],
        customer_id: int,
        *,
        dry_run: bool,
    ) -> Tuple[AppDirectCall, Optional[Tuple[int, str]]]:
        payload = self._build_order_payload(row, customer_id)
        path = "/api/v2/orders"
        row_id = row.get("row_id") or ""
        session_id = row.get("session_id") or ""
        if dry_run:
            call = await self._log_dry_run(
                endpoint=f"POST {path}",
                request_body=payload,
                row_id=row_id,
                session_id=session_id,
            )
            return call, None
        call = await self._live_request(
            "POST", path, json_body=payload, row_id=row_id, session_id=session_id
        )
        body = call.response_body
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            data = body if isinstance(body, dict) else {}
        try:
            order_id = int(data.get("id") or 0)
        except (TypeError, ValueError):
            order_id = 0
        order_number = str(data.get("orderNumber") or "")
        if order_id or order_number:
            return call, (order_id, order_number)
        return call, None

    async def _step_create_order_detail(
        self,
        row: Dict[str, Any],
        template: Dict[str, Any],
        *,
        order_id_or_number: str,
        customer_location_ids: List[int],
        dry_run: bool,
    ) -> AppDirectCall:
        payload = self._build_order_detail_payload(
            row,
            template,
            order_id_or_number=order_id_or_number,
            customer_location_ids=customer_location_ids,
        )
        path = "/api/v2/orderDetails"
        row_id = row.get("row_id") or ""
        session_id = row.get("session_id") or ""
        if dry_run:
            return await self._log_dry_run(
                endpoint=f"POST {path}",
                request_body=payload,
                row_id=row_id,
                session_id=session_id,
            )
        return await self._live_request(
            "POST", path, json_body=payload, row_id=row_id, session_id=session_id
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def push_order(
        self,
        row: Dict[str, Any],
        *,
        dry_run: bool = False,
        template: Optional[Dict[str, Any]] = None,
    ) -> AppDirectCall:
        """Push an approved row's output to AppDirect via the full chain.

        The chain decomposes into up to four HTTP calls:

          1.  POST  /api/services/app/Customers/v2/create
                — skipped when audit_metadata.matched_candidate_id is set.
          2.  GET   /api/services/app/Customers/v2/{customerId}/locations
                — fuzzy address match against existing locations.
          3.  POST  /api/services/app/Customers/v2/{customerId}/locations
                — only when step 2 returns no match.
          4.  POST  /api/v2/orders
          5.  POST  /api/v2/orderDetails

        Each step writes its own ``match_appdirect_calls`` doc. The chain
        HALTS on the first non-2xx response or connection error.

        Returns the **terminal** AppDirectCall — typically the OrderDetail
        create, or the step that failed. Route handlers consult the call's
        ``response_status`` / ``error`` to decide whether to flip
        ``pushed_to_appdirect`` on the row.

        Always dry-runs when ``settings.appdirect.live_post_enabled`` is
        False, regardless of the ``dry_run`` argument. In dry-run mode the
        chain still walks every step so Push History reflects what WOULD
        happen — no early exit.

        Args:
            row: Persisted ``match_rows`` doc (Pydantic-validated upstream).
            dry_run: Force dry-run even when live_post_enabled is True.
            template: Provider template dict; FK overrides come from here.
                Defaults to ``{}`` — resolvers fall back to cached lookups.
        """
        row_id = row.get("row_id") or ""
        session_id = row.get("session_id") or ""
        template = template or {}
        forced_dry_run = dry_run or not self._live_post_enabled

        # Status gate — refuse upfront so we never fire HTTP for a row the
        # reviewer hasn't approved.
        status = (row.get("status") or "").strip()
        if status not in _PUSHABLE_STATUSES:
            return await self._log_dry_run(
                endpoint="REFUSED: status not pushable",
                request_body={"status": status, "row_id": row_id},
                row_id=row_id,
                session_id=session_id,
                error=(
                    f"row status '{status}' is not pushable; only "
                    "approved/edited/create_new/create_new_order can be pushed"
                ),
            )

        audit = row.get("audit_metadata") or {}
        output = row.get("output") or {}
        top_level = output.get("top_level") or {}
        cust_block = top_level.get("customer") or {}

        # Decide whether to skip customer creation.
        matched_candidate_id = str(audit.get("matched_candidate_id") or "").strip()
        if not matched_candidate_id:
            # Also accept output.top_level.customer.appdirect_customer_id
            # (the source POC's stamped field for previously-pushed rows).
            existing = cust_block.get("appdirect_customer_id")
            if existing:
                matched_candidate_id = str(existing).strip()

        # Step 1 — Customer.
        customer_id: Optional[int] = None
        if matched_candidate_id:
            try:
                customer_id = int(matched_candidate_id)
            except ValueError:
                customer_id = None

        if customer_id is None:
            cust_call, created_id = await self._step_create_customer(
                row, template, dry_run=forced_dry_run
            )
            if not forced_dry_run and not _is_success(cust_call):
                return cust_call  # halt
            customer_id = created_id or 0

        # Step 2 — Location.find. Always walked so dry-run reflects every step;
        # in live mode with a real customer_id we use the response, otherwise
        # we fall through to Location.create.
        existing_location_id: Optional[int] = None
        find_customer_id = customer_id if customer_id else 0
        find_call, found_id = await self._step_find_location(
            row, find_customer_id, dry_run=forced_dry_run
        )
        if not forced_dry_run and customer_id:
            # 4xx/5xx (other than 404 = no locations) halts the chain.
            if not _is_success(find_call) and find_call.response_status != 404:
                return find_call
        existing_location_id = found_id

        location_ids: List[int] = []
        if existing_location_id:
            location_ids = [existing_location_id]
        else:
            # Step 3 — Location.create.
            loc_call, new_loc_id = await self._step_create_location(
                row, customer_id or 0, dry_run=forced_dry_run
            )
            if not forced_dry_run and not _is_success(loc_call):
                return loc_call
            if new_loc_id:
                location_ids = [new_loc_id]

        # Step 4 — Order.create.
        order_call, order_ref = await self._step_create_order(
            row, customer_id or 0, dry_run=forced_dry_run
        )
        if not forced_dry_run and not _is_success(order_call):
            return order_call

        if forced_dry_run:
            order_id_or_number = "DRY_RUN_ORDER_ID"
        else:
            order_id, order_number = order_ref or (0, "")
            order_id_or_number = order_number or (str(order_id) if order_id else "0")

        # Step 5 — OrderDetail.create — terminal call, the one we return.
        return await self._step_create_order_detail(
            row,
            template,
            order_id_or_number=order_id_or_number,
            customer_location_ids=location_ids or [0],
            dry_run=forced_dry_run,
        )

    # ------------------------------------------------------------------
    # Standalone utility methods — kept for backward compat with callers
    # that aren't going through push_order (settings test endpoints, etc.).
    # ------------------------------------------------------------------

    async def lookup_customer(
        self,
        name: str,
        *,
        row_id: str = "",
        session_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """GET /customers?name=<urlencoded>. Returns the first match or None.

        Always logged to match_appdirect_calls. When live_post_enabled is
        False, no real HTTP call is made — a dry_run log is written and
        None is returned.
        """
        encoded = quote(name or "", safe="")
        path = f"/customers?name={encoded}"
        endpoint_label = f"GET {path}"
        request_body: Dict[str, Any] = {"name": name}

        if not self._live_post_enabled:
            await self._log_dry_run(
                endpoint=endpoint_label,
                request_body=request_body,
                row_id=row_id,
                session_id=session_id,
            )
            return None

        call = await self._live_request(
            "GET",
            path,
            json_body=None,
            row_id=row_id,
            session_id=session_id,
        )
        body = call.response_body
        if isinstance(body, list) and body:
            first = body[0]
            return first if isinstance(first, dict) else None
        if isinstance(body, dict):
            # Some AppDirect endpoints wrap collections in items/data.
            items = body.get("items") or body.get("data")
            if isinstance(items, list) and items:
                first = items[0]
                return first if isinstance(first, dict) else None
            # Bare match dict — return it.
            if body.get("id") or body.get("customerId"):
                return body
        return None

    async def create_customer(
        self,
        payload: Dict[str, Any],
        *,
        row_id: str = "",
        session_id: str = "",
    ) -> Dict[str, Any]:
        """POST /customers. Returns the created customer dict (or an empty
        dict if dry_run / live_post_enabled is False).

        Always logged.
        """
        endpoint_label = "POST /customers"
        if not self._live_post_enabled:
            await self._log_dry_run(
                endpoint=endpoint_label,
                request_body=payload,
                row_id=row_id,
                session_id=session_id,
            )
            return {}

        call = await self._live_request(
            "POST",
            "/customers",
            json_body=payload,
            row_id=row_id,
            session_id=session_id,
        )
        body = call.response_body
        return body if isinstance(body, dict) else {}

    async def fetch_lookup_tables(self) -> Dict[str, Any]:
        """Pull every AppDirect lookup table the Settings UI needs.

        Fault-tolerant: each endpoint is fetched independently; failures are
        recorded under ``failed`` but don't abort the whole sync. Mirrors the
        MATCH source ``GET /api/settings/appdirect-lookups`` behavior.

        Returns:
            {
                "synced": ["products", "users", ...],
                "failed": {"contractTerms": "HTTP 404"},
                "cached": {...},          # the new merged cache
                "lookups_fetched_at": "<iso>",
            }

        Caller (1D's settings route) persists ``cached`` into
        appdirect.cached_lookups and ``lookups_fetched_at`` into
        appdirect.lookups_fetched_at.

        Every individual endpoint fetch is also logged to
        match_appdirect_calls with row_id="" and session_id="" (these are
        not row-scoped operations).
        """
        synced: List[str] = []
        failed: Dict[str, str] = {}
        cache: Dict[str, Any] = {}

        if not self._base_url or not self._bearer_token:
            return {
                "synced": [],
                "failed": {
                    "_config": (
                        "AppDirect base_url and bearer_token must be configured "
                        "before syncing lookup tables."
                    )
                },
                "cached": {},
                "lookups_fetched_at": _now_iso(),
            }

        for key, path in _LOOKUP_ENDPOINTS.items():
            call = await self._live_request(
                "GET",
                path,
                json_body=None,
                row_id="",
                session_id="",
                timeout=_LOOKUP_TIMEOUT_SECONDS,
            )
            status = call.response_status
            if call.error:
                failed[key] = call.error[:120]
            elif status is None:
                failed[key] = "no response"
            elif 200 <= status < 300:
                cache[key] = call.response_body
                synced.append(key)
            else:
                failed[key] = f"HTTP {status}"

        return {
            "synced": synced,
            "failed": failed,
            "cached": cache,
            "lookups_fetched_at": _now_iso(),
        }


# ----------------------------------------------------------------------------
# Module-level helpers — outside the class so step methods can use them.
# ----------------------------------------------------------------------------


def _is_success(call: AppDirectCall) -> bool:
    """True iff this live AppDirectCall represents a 2xx with no error.

    Connection errors (where response_status is None) are NOT success.
    """
    if call.error:
        return False
    status = call.response_status
    return status is not None and 200 <= status < 300


def _extract_id(body: Any) -> Optional[int]:
    """Pull the top-level integer ``id`` out of an AppDirect response.

    Handles both the bare ``{"id": ...}`` shape (CustomerModel per Swagger)
    and the ``{"data": {"id": ...}}`` envelope some endpoints use.
    """
    if isinstance(body, dict):
        if body.get("id") is not None:
            try:
                return int(body["id"])
            except (TypeError, ValueError):
                return None
        data = body.get("data")
        if isinstance(data, dict) and data.get("id") is not None:
            try:
                return int(data["id"])
            except (TypeError, ValueError):
                return None
    return None
