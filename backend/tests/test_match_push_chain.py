"""End-to-end tests for the AppDirect push chain in ``AppDirectClient``.

The chain decomposes a single approved row into the real AppDirect Provider
Sales API sequence:

    1. POST /api/services/app/Customers/v2/create
       (skipped when audit_metadata.matched_candidate_id is set)
    2. GET  /api/services/app/Customers/v2/{customerId}/locations
    3. POST /api/services/app/Customers/v2/{customerId}/locations
       (only when step 2 returns no match)
    4. POST /api/v2/orders
    5. POST /api/v2/orderDetails

Each step writes its own doc to ``match_appdirect_calls``. The chain HALTS
on the first non-2xx response or connection error.

These tests do NOT hit the live mock AppDirect container — they monkey-patch
``httpx.AsyncClient.request`` so the chain can be exercised in-process. For
true live coverage, run a row through the local match-mock-appdirect stack
manually.

Run via:
    cd backend
    python -m pytest tests/test_match_push_chain.py -v
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

# Tests live in backend/tests/ — add backend/ to sys.path so the imports
# resolve the same way they do under the running server.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.match.appdirect_client import AppDirectClient  # noqa: E402
from services.match.crypto import MASKED_PASSWORD_SENTINEL  # noqa: E402
from services.match.models import Settings  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_client(settings: Settings, *, db, actor: str = "rep@simplimeta.test") -> AppDirectClient:
    """Construct an AppDirectClient with a synthetic bearer.

    The Settings ``bearer_token`` field is left blank to avoid triggering
    ``decrypt_value`` (which would need a real Fernet ciphertext). We inject
    a synthetic plaintext bearer after construction so ``_live_request``'s
    sanity check passes and tests can exercise the full chain.
    """
    client = AppDirectClient(settings, db=db, actor=actor)
    client._bearer_token = "test-bearer-not-real"  # noqa: SLF001 — test injection
    return client


def _make_settings(*, live: bool = True) -> Settings:
    """Build a Settings instance with a populated AppDirect block.

    ``bearer_token`` is left empty so ``decrypt_value`` is never called — the
    push chain only needs ``base_url`` to satisfy ``_live_request``'s sanity
    check. The chain receives whatever the mocked httpx layer returns.
    """
    return Settings.model_validate(
        {
            "appdirect": {
                "base_url": "https://appdirect.example.test",
                "bearer_token": "",
                "live_post_enabled": live,
                "multipass_enabled": False,
                "defaults": {
                    "contract_type_id_new": 1,
                    "contract_term_id_12mo": 11,
                    "contract_term_id_24mo": 12,
                    "contract_term_id_36mo": 13,
                    "sales_agent_id": 555,
                },
                "cached_lookups": {
                    "advisors": [{"id": 101, "name": "Default Advisor"}],
                    "providers": [{"id": 201, "name": "Acme Provider"}],
                    "products": [
                        {"id": 301, "name": "Voice Plan", "providerId": 201},
                    ],
                    "contractTypes": [{"id": 1, "name": "New"}],
                    "contractTerms": [
                        {"id": 11, "name": "12 Months", "monthsCount": 12},
                        {"id": 13, "name": "36 Months", "monthsCount": 36},
                    ],
                    "users": [
                        {"id": 901, "email": "rep@simplimeta.test"},
                    ],
                },
            }
        }
    )


def _make_row(
    *,
    status: str = "approved",
    matched_candidate_id: str = "",
) -> Dict[str, Any]:
    """A minimally-realistic Contract H row for the push chain to chew on."""
    return {
        "row_id": "m_row_test01",
        "session_id": "m_sess_test01",
        "status": status,
        "audit_metadata": {"matched_candidate_id": matched_candidate_id},
        "output": {
            "top_level": {
                "customer": {
                    "name": "Acme Corp",
                    "account_manager_email": "",
                },
                "location": {
                    "street": "123 Main St",
                    "city": "Boston",
                    "state": "MA",
                    "zip": "02110",
                },
                "sales_rep": "rep@simplimeta.test",
            },
            "order_details": {
                "product_name": "Voice Plan",
                "provider_name": "Acme Provider",
                "contract_type": "New",
                "contract_term": "36 Months",
                "mrgmv": "1,234.50",
                "nrc": "0",
                "contract_signed": "2026-05-01T00:00:00Z",
                "commission_ban": "BAN-12345",
                "provider_sales_order_id": "PSO-99",
            },
            "order_attributes": {
                "advisor_order_id": "AOI-77",
            },
        },
    }


class _FakeAppDirect:
    """Sequenced mock that responds to a planned list of ``(status, body)``
    tuples in the order the chain calls them.

    Each entry also records the (method, url) it served so tests can assert
    on which endpoints were hit.
    """

    def __init__(self, plan: List[Tuple[int, Any]]) -> None:
        self._plan = list(plan)
        self.calls: List[Tuple[str, str, Any]] = []  # (method, url, json_body)

    def _next(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, str(request.url), _safe_read_json(request)))
        if not self._plan:
            raise AssertionError(
                f"Unexpected extra call to mock AppDirect: {request.method} {request.url}"
            )
        status, body = self._plan.pop(0)
        return httpx.Response(status_code=status, json=body)


def _safe_read_json(request: httpx.Request) -> Any:
    """Decode the request JSON body if any. GET requests have empty bodies."""
    if not request.content:
        return None
    try:
        import json

        return json.loads(request.content)
    except Exception:
        return None


def _install_mock_httpx(monkeypatch, mock: _FakeAppDirect) -> None:
    """Patch ``httpx.AsyncClient.request`` so the push chain sees the mock.

    We monkey-patch on the class instead of the per-instance method because
    ``_live_request`` builds a fresh ``httpx.AsyncClient`` for every call.
    """

    async def fake_request(self, **kwargs):  # type: ignore[override]
        method = kwargs["method"]
        url = kwargs["url"]
        json_body = kwargs.get("json")
        request = httpx.Request(method, url, json=json_body)
        return mock._next(request)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)


def _make_db() -> Tuple[MagicMock, List[Dict[str, Any]]]:
    """In-memory stand-in for the motor ``db`` handle.

    Returns ``(db, log)`` where ``log`` accumulates every doc the client
    inserts into ``match_appdirect_calls``.
    """
    log: List[Dict[str, Any]] = []
    coll = MagicMock()

    async def fake_insert_one(doc):
        log.append(doc)

    coll.insert_one = AsyncMock(side_effect=fake_insert_one)
    db = MagicMock()
    db.match_appdirect_calls = coll
    return db, log


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_chain_clean_match_creates_location_and_order(monkeypatch):
    """A row with a matched_candidate_id skips Customer.create.

    Plan:
      - GET locations → empty list (no address match)
      - POST locations → created (location 7001)
      - POST orders → created (order 8001 / "ORD-8001")
      - POST orderDetails → created (detail 9001)
    Expected: 4 log docs, NO POST /Customers/v2/create.
    """
    db, log = _make_db()
    settings = _make_settings(live=True)
    mock = _FakeAppDirect(
        [
            (200, []),  # GET locations — no match
            (200, [{"id": 7001, "locationName": "Acme Corp"}]),  # POST location
            (201, {"data": {"id": 8001, "orderNumber": "ORD-8001"}}),  # POST order
            (201, {"data": [{"id": 9001}]}),  # POST orderDetails
        ]
    )
    _install_mock_httpx(monkeypatch, mock)

    client = _make_client(settings, db=db)
    row = _make_row(status="approved", matched_candidate_id="10002")

    terminal = await client.push_order(row, template={})

    # Terminal call is the OrderDetail create — the chain's last step.
    assert terminal.endpoint == "POST /api/v2/orderDetails"
    assert terminal.response_status == 201

    # 4 calls, exactly — no Customer.create.
    endpoints_called = [c[1] for c in mock.calls]
    assert len(endpoints_called) == 4
    assert not any("/Customers/v2/create" in u for u in endpoints_called)
    assert any(u.endswith("/api/services/app/Customers/v2/10002/locations") for u in endpoints_called)
    assert any(u.endswith("/api/v2/orders") for u in endpoints_called)
    assert any(u.endswith("/api/v2/orderDetails") for u in endpoints_called)

    # Same in the log collection — 4 docs.
    assert len(log) == 4
    logged_endpoints = [d["endpoint"] for d in log]
    assert "GET /api/services/app/Customers/v2/10002/locations" in logged_endpoints
    assert "POST /api/services/app/Customers/v2/10002/locations" in logged_endpoints
    assert "POST /api/v2/orders" in logged_endpoints
    assert "POST /api/v2/orderDetails" in logged_endpoints

    # Token never leaks — every doc carries the sentinel.
    for d in log:
        assert d["request_headers_redacted"]["Authorization"] == MASKED_PASSWORD_SENTINEL

    # OrderDetail payload reflects the resolved FKs from cached lookups.
    detail_doc = next(d for d in log if d["endpoint"] == "POST /api/v2/orderDetails")
    body = detail_doc["request_body"]
    assert body["productId"] == 301
    assert body["providerId"] == 201
    assert body["contract"]["contractTypeId"] == 1
    assert body["contract"]["contractTermId"] == 13
    assert body["customerLocationIds"] == [7001]
    assert body["idOrOrderNumber"] == "ORD-8001"


@pytest.mark.asyncio
async def test_push_chain_no_match_creates_customer_too(monkeypatch):
    """A row with status=create_new and no matched_candidate_id triggers
    customer creation before everything else.

    Plan:
      - POST Customers/v2/create → created (customer 6001)
      - GET locations → empty
      - POST locations → 7002
      - POST orders → 8002 / ORD-8002
      - POST orderDetails → 9002
    Expected: 5 log docs.
    """
    db, log = _make_db()
    settings = _make_settings(live=True)
    mock = _FakeAppDirect(
        [
            (200, {"id": 6001, "name": "Acme Corp"}),  # POST Customers/v2/create
            (200, []),  # GET locations
            (200, [{"id": 7002}]),  # POST locations
            (201, {"data": {"id": 8002, "orderNumber": "ORD-8002"}}),  # POST orders
            (201, {"data": [{"id": 9002}]}),  # POST orderDetails
        ]
    )
    _install_mock_httpx(monkeypatch, mock)

    client = _make_client(settings, db=db)
    row = _make_row(status="create_new", matched_candidate_id="")

    terminal = await client.push_order(row, template={})

    assert terminal.endpoint == "POST /api/v2/orderDetails"
    assert terminal.response_status == 201

    assert len(log) == 5
    logged_endpoints = [d["endpoint"] for d in log]
    assert logged_endpoints == [
        "POST /api/services/app/Customers/v2/create",
        "GET /api/services/app/Customers/v2/6001/locations",
        "POST /api/services/app/Customers/v2/6001/locations",
        "POST /api/v2/orders",
        "POST /api/v2/orderDetails",
    ]

    # Customer payload uses the cached advisor id (101).
    cust_doc = log[0]
    assert cust_doc["request_body"]["advisorId"] == 101
    assert cust_doc["request_body"]["name"] == "Acme Corp"
    assert cust_doc["request_body"]["createDefaultLocation"] is False

    # Order payload uses the resolved sales-agent override (555 from defaults).
    order_doc = log[3]
    assert order_doc["request_body"]["customerId"] == 6001
    assert order_doc["request_body"]["salesAgentId"] == 555


@pytest.mark.asyncio
async def test_push_chain_halts_on_step_failure(monkeypatch):
    """A 500 on Customer.create halts the chain — no further steps run."""
    db, log = _make_db()
    settings = _make_settings(live=True)
    mock = _FakeAppDirect(
        [
            (500, {"error": "internal server error"}),
        ]
    )
    _install_mock_httpx(monkeypatch, mock)

    client = _make_client(settings, db=db)
    row = _make_row(status="create_new", matched_candidate_id="")

    terminal = await client.push_order(row, template={})

    # Terminal call is the failed Customer.create — chain halted there.
    assert terminal.endpoint == "POST /api/services/app/Customers/v2/create"
    assert terminal.response_status == 500
    # Only one HTTP attempt was made.
    assert len(mock.calls) == 1
    # And only one log doc was written.
    assert len(log) == 1


@pytest.mark.asyncio
async def test_push_chain_refuses_non_pushable_status(monkeypatch):
    """A row in status=needs_review never makes any HTTP calls."""
    db, log = _make_db()
    settings = _make_settings(live=True)
    mock = _FakeAppDirect([])  # no responses planned — any call would AssertionError
    _install_mock_httpx(monkeypatch, mock)

    client = _make_client(settings, db=db)
    row = _make_row(status="needs_review", matched_candidate_id="10002")

    terminal = await client.push_order(row, template={})

    assert terminal.endpoint == "REFUSED: status not pushable"
    assert terminal.error and "not pushable" in terminal.error
    assert mock.calls == []
    # The refusal itself is logged so Push History sees it.
    assert len(log) == 1


@pytest.mark.asyncio
async def test_push_chain_dry_run_logs_every_step(monkeypatch):
    """When dry_run=True the chain walks every step but makes no HTTP calls.

    This guards the "Push History reflects what WOULD happen" promise.
    """
    db, log = _make_db()
    settings = _make_settings(live=True)
    mock = _FakeAppDirect([])  # no planned responses; chain must skip httpx
    _install_mock_httpx(monkeypatch, mock)

    client = _make_client(settings, db=db)
    row = _make_row(status="create_new", matched_candidate_id="")

    terminal = await client.push_order(row, template={}, dry_run=True)

    # No actual HTTP calls — mock was never invoked.
    assert mock.calls == []
    # But every dry-run step landed in the log.
    assert len(log) == 5
    assert all(d["mode"] == "dry_run" for d in log)
    assert terminal.mode == "dry_run"
