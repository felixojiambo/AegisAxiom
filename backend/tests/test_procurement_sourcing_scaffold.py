"""Tests for the Mode B procurement sourcing scaffold (Ticket 9).

Covers:
- Webhook signature helper (configured / unconfigured / valid /
  invalid / scheme-prefix tolerance).
- Bot client (not configured / configured / transport error /
  bot 4xx / bot 5xx / success).
- The 8 AEGISAXIOM-side routes (create / list / detail / retry /
  webhook / select / mark ordered / mark delivered).
- Audit row presence on every state-change.
- Tenant safety via opportunity ownership.
- Mode B default: every quote arrives selected=False.
- Source-level checks on route surfaces and the scaffold doc.

Real HTTP calls are mocked. Real Google APIs and real vendor
portals are not contacted. No real vendor names appear in
fixtures.
"""

import hashlib
import hmac
import json
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest


# ---------------------------------------------------------------------------
# Webhook signature helper
# ---------------------------------------------------------------------------


def test_webhook_helper_reports_configured_only_when_secret_set(monkeypatch):
    from services.procurement_webhook_security import is_webhook_configured

    monkeypatch.delenv("PROCUREMENT_BOT_WEBHOOK_SECRET", raising=False)
    assert is_webhook_configured() is False

    monkeypatch.setenv("PROCUREMENT_BOT_WEBHOOK_SECRET", "test-secret-do-not-use")
    assert is_webhook_configured() is True


def test_webhook_helper_treats_empty_secret_as_unset(monkeypatch):
    from services.procurement_webhook_security import is_webhook_configured

    monkeypatch.setenv("PROCUREMENT_BOT_WEBHOOK_SECRET", "   ")
    assert is_webhook_configured() is False


def test_webhook_signature_round_trip(monkeypatch):
    """A signature computed with the secret matches when verified
    against the same secret."""
    from services.procurement_webhook_security import (
        compute_signature,
        verify_signature,
    )

    monkeypatch.setenv("PROCUREMENT_BOT_WEBHOOK_SECRET", "round-trip-secret")
    body = b'{"sourcing_request_id":"srcreq_synth"}'
    sig = compute_signature(body, "round-trip-secret")
    assert verify_signature(body, sig) is True


def test_webhook_signature_tolerates_sha256_prefix(monkeypatch):
    """The bot may prefix the hex digest with sha256= per GitHub
    webhook convention. The verifier strips the scheme prefix."""
    from services.procurement_webhook_security import (
        compute_signature,
        verify_signature,
    )

    monkeypatch.setenv("PROCUREMENT_BOT_WEBHOOK_SECRET", "prefix-secret")
    body = b"payload"
    sig = "sha256=" + compute_signature(body, "prefix-secret")
    assert verify_signature(body, sig) is True


def test_webhook_signature_rejects_mismatch(monkeypatch):
    from services.procurement_webhook_security import verify_signature

    monkeypatch.setenv("PROCUREMENT_BOT_WEBHOOK_SECRET", "real-secret")
    assert verify_signature(b"payload", "deadbeef") is False
    assert verify_signature(b"payload", None) is False
    assert verify_signature(b"payload", "") is False


def test_webhook_signature_returns_false_when_secret_unset(monkeypatch):
    """Safety net: the route gates the request before calling the
    verifier, but the verifier itself must never accept any
    signature when the secret is missing."""
    from services.procurement_webhook_security import (
        compute_signature,
        verify_signature,
    )

    monkeypatch.delenv("PROCUREMENT_BOT_WEBHOOK_SECRET", raising=False)
    sig = compute_signature(b"payload", "any-key")
    assert verify_signature(b"payload", sig) is False


# ---------------------------------------------------------------------------
# Procurement bot client
# ---------------------------------------------------------------------------


def test_bot_client_reports_unconfigured_when_env_missing(monkeypatch):
    from services.procurement_bot_client import is_bot_configured

    monkeypatch.delenv("PROCUREMENT_BOT_BASE_URL", raising=False)
    monkeypatch.delenv("PROCUREMENT_BOT_API_KEY", raising=False)
    assert is_bot_configured() is False


def test_bot_client_requires_both_url_and_key(monkeypatch):
    from services.procurement_bot_client import is_bot_configured

    monkeypatch.setenv("PROCUREMENT_BOT_BASE_URL", "https://bot.example.test")
    monkeypatch.delenv("PROCUREMENT_BOT_API_KEY", raising=False)
    assert is_bot_configured() is False

    monkeypatch.setenv("PROCUREMENT_BOT_API_KEY", "test-key")
    assert is_bot_configured() is True


def test_bot_client_build_payload_strips_internal_fields():
    """The BOM payload sent to the bot includes only fields the
    bot needs. Internal IDs, customer-facing wording, audit notes,
    and tenant ids must not appear."""
    from services.procurement_bot_client import build_submission_payload

    line_items = [
        {
            "line_item_id": "li_a",
            "category": "hardware",
            "subcategory": "switches",
            "item_name": "Synthetic Switch",
            "quantity": 5,
            "unit": "each",
            "supplier_sku": "SKU-A",
            "cost_price": 1000.0,
            "tenant_id": "tenant_synth",
        }
    ]
    payload = build_submission_payload(
        sourcing_request_id="srcreq_synth",
        opportunity_id="opp_synth",
        line_items=line_items,
    )
    assert payload["sourcing_request_id"] == "srcreq_synth"
    assert payload["opportunity_id"] == "opp_synth"
    assert payload["callback_identifier"] == "srcreq_synth"
    assert len(payload["lines"]) == 1
    line = payload["lines"][0]
    assert line["material_line_id"] == "li_a"
    assert line["item_name"] == "Synthetic Switch"
    # Pricing and tenant id are NOT sent to the bot.
    assert "cost_price" not in line
    assert "tenant_id" not in line


@pytest.mark.asyncio
async def test_bot_client_returns_unconfigured_when_env_missing(monkeypatch):
    from services.procurement_bot_client import submit_sourcing_request

    monkeypatch.delenv("PROCUREMENT_BOT_BASE_URL", raising=False)
    monkeypatch.delenv("PROCUREMENT_BOT_API_KEY", raising=False)

    result = await submit_sourcing_request({})
    assert result.configured is False
    assert result.accepted is False
    assert result.error_reason == "bot_not_configured"


@pytest.mark.asyncio
async def test_bot_client_accepts_on_2xx(monkeypatch):
    from services.procurement_bot_client import submit_sourcing_request

    monkeypatch.setenv("PROCUREMENT_BOT_BASE_URL", "https://bot.example.test")
    monkeypatch.setenv("PROCUREMENT_BOT_API_KEY", "test-key")

    def handler(request):
        # Authorization header is set but never logged.
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content.decode("utf-8"))
        assert body["sourcing_request_id"] == "srcreq_synth"
        return httpx.Response(
            200,
            json={"bot_request_id": "bot_xyz", "status": "queued"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await submit_sourcing_request(
            {"sourcing_request_id": "srcreq_synth", "lines": []},
            client=client,
        )
    assert result.configured is True
    assert result.accepted is True
    assert result.bot_request_id == "bot_xyz"
    assert result.error_reason is None


@pytest.mark.asyncio
async def test_bot_client_maps_4xx_to_bot_4xx(monkeypatch):
    from services.procurement_bot_client import submit_sourcing_request

    monkeypatch.setenv("PROCUREMENT_BOT_BASE_URL", "https://bot.example.test")
    monkeypatch.setenv("PROCUREMENT_BOT_API_KEY", "test-key")

    transport = httpx.MockTransport(
        lambda _r: httpx.Response(422, json={"detail": "invalid payload"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await submit_sourcing_request({}, client=client)
    assert result.configured is True
    assert result.accepted is False
    assert result.error_reason == "bot_4xx"


@pytest.mark.asyncio
async def test_bot_client_maps_5xx_to_bot_5xx(monkeypatch):
    from services.procurement_bot_client import submit_sourcing_request

    monkeypatch.setenv("PROCUREMENT_BOT_BASE_URL", "https://bot.example.test")
    monkeypatch.setenv("PROCUREMENT_BOT_API_KEY", "test-key")

    transport = httpx.MockTransport(
        lambda _r: httpx.Response(503, json={"detail": "down"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await submit_sourcing_request({}, client=client)
    assert result.configured is True
    assert result.accepted is False
    assert result.error_reason == "bot_5xx"


@pytest.mark.asyncio
async def test_bot_client_maps_transport_error_to_transport_error(monkeypatch):
    from services.procurement_bot_client import submit_sourcing_request

    monkeypatch.setenv("PROCUREMENT_BOT_BASE_URL", "https://bot.example.test")
    monkeypatch.setenv("PROCUREMENT_BOT_API_KEY", "test-key")

    def handler(_request):
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await submit_sourcing_request({}, client=client)
    assert result.configured is True
    assert result.accepted is False
    assert result.error_reason == "transport_error"


# ---------------------------------------------------------------------------
# Route-level: create sourcing request (Mode B, bot not configured)
# ---------------------------------------------------------------------------


class _FakeAsyncCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._limit = None
        self._sort_field = None

    def sort(self, *_args, **_kwargs):
        return self

    def to_list(self, limit=None):
        async def _coro():
            if limit is not None:
                return list(self._docs)[:limit]
            return list(self._docs)
        return _coro()


class _FakeCollection:
    """Generic Mongo-like fake collection. Stores docs in a list and
    supports find / find_one / insert_one / update_one / update_many
    with the simple equality-filter shapes the routes use."""

    def __init__(self, docs=None):
        self._docs: list[dict] = [dict(d) for d in (docs or [])]
        self.inserted: list[dict] = []
        self.updates: list[tuple[dict, dict]] = []

    @staticmethod
    def _matches(doc, query):
        if not isinstance(query, dict):
            return False
        for k, v in query.items():
            if isinstance(v, dict) and "$in" in v:
                if doc.get(k) not in v["$in"]:
                    return False
            elif isinstance(v, dict) and "$ne" in v:
                if doc.get(k) == v["$ne"]:
                    return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    async def find_one(self, query, _projection=None):
        for d in self._docs:
            if self._matches(d, query):
                return dict(d)
        return None

    def find(self, query, _projection=None):
        matched = [dict(d) for d in self._docs if self._matches(d, query)]
        return _FakeAsyncCursor(matched)

    async def insert_one(self, doc):
        self._docs.append(dict(doc))
        self.inserted.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("sourcing_request_id"))

    async def update_one(self, query, update):
        for d in self._docs:
            if self._matches(d, query):
                d.update(update.get("$set", {}))
                self.updates.append((query, update.get("$set", {})))
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def update_many(self, query, update):
        modified = 0
        for d in self._docs:
            if self._matches(d, query):
                d.update(update.get("$set", {}))
                modified += 1
        return SimpleNamespace(modified_count=modified)


def _user(perms, user_id="u_synth", email="u@aegisaxiom.test"):
    return SimpleNamespace(
        user_id=user_id, email=email, role="sales_ops", permissions=list(perms)
    )


def _build_procurement_db(
    opportunity=None,
    line_items=None,
    sourcing_requests=None,
    quote_options=None,
):
    """Stitch a fake db with all the procurement collections."""
    return SimpleNamespace(
        opportunities=_FakeCollection([opportunity] if opportunity else []),
        opportunity_line_items=_FakeCollection(line_items or []),
        procurement_sourcing_requests=_FakeCollection(sourcing_requests or []),
        procurement_quote_options=_FakeCollection(quote_options or []),
    )


@pytest.mark.asyncio
async def test_create_sourcing_request_lands_in_review_required_when_bot_unconfigured(monkeypatch):
    """Mode B: when the bot is not configured, every new sourcing
    request lands in review_required with the
    manual_review_required flag set so the Procurement queue
    surfaces it."""
    from routes.sales_ops_procurement import create_procurement_sourcing_request

    monkeypatch.delenv("PROCUREMENT_BOT_BASE_URL", raising=False)
    monkeypatch.delenv("PROCUREMENT_BOT_API_KEY", raising=False)

    captured: list[dict] = []
    async def _fake_audit(**kwargs):
        captured.append(kwargs)
    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action", _fake_audit
    )

    db = _build_procurement_db(
        opportunity={"opportunity_id": "opp_synth", "name": "Synth"},
        line_items=[
            {
                "line_item_id": "li_a",
                "opportunity_id": "opp_synth",
                "category": "hardware",
                "item_name": "Synthetic Switch",
                "quantity": 1,
                "unit": "each",
            }
        ],
    )
    body = {
        "opportunity_id": "opp_synth",
        "sow_id": "sow_synth",
        "line_item_ids": ["li_a"],
    }
    user = _user(["sales_ops.procurement.create"])

    fresh = await create_procurement_sourcing_request(
        data=body, db=db, current_user=user
    )
    assert fresh["status"] == "review_required"
    assert fresh["manual_review_required"] is True
    assert fresh["manual_review_reason"] == "bot_not_configured"
    assert fresh["line_item_ids"] == ["li_a"]
    # Audit row written.
    actions = [c["action"] for c in captured]
    assert "sales_ops.procurement.sourcing_requested" in actions
    assert "sales_ops.procurement.bot_submission_attempted" not in actions


@pytest.mark.asyncio
async def test_create_sourcing_request_drops_line_items_from_other_opportunities(monkeypatch):
    """A caller cannot smuggle in a line item that does not belong
    to the requested opportunity. The route silently drops the
    invalid id from line_item_ids."""
    from routes.sales_ops_procurement import create_procurement_sourcing_request

    monkeypatch.delenv("PROCUREMENT_BOT_BASE_URL", raising=False)
    monkeypatch.delenv("PROCUREMENT_BOT_API_KEY", raising=False)
    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action",
        AsyncMock(return_value=None),
    )

    db = _build_procurement_db(
        opportunity={"opportunity_id": "opp_synth", "name": "Synth"},
        line_items=[
            {
                "line_item_id": "li_owned",
                "opportunity_id": "opp_synth",
                "category": "hardware",
                "item_name": "Owned",
            },
            {
                "line_item_id": "li_other_opp",
                "opportunity_id": "opp_OTHER",
                "category": "hardware",
                "item_name": "Other",
            },
        ],
    )
    body = {
        "opportunity_id": "opp_synth",
        "line_item_ids": ["li_owned", "li_other_opp"],
    }
    fresh = await create_procurement_sourcing_request(
        data=body, db=db, current_user=_user(["sales_ops.procurement.create"])
    )
    assert fresh["line_item_ids"] == ["li_owned"]


@pytest.mark.asyncio
async def test_create_sourcing_request_requires_permission():
    from fastapi import HTTPException
    from routes.sales_ops_procurement import create_procurement_sourcing_request

    db = _build_procurement_db(opportunity={"opportunity_id": "opp_synth"})
    user = _user([])  # No permissions
    raised = False
    try:
        await create_procurement_sourcing_request(
            data={"opportunity_id": "opp_synth"}, db=db, current_user=user
        )
    except HTTPException as e:
        raised = True
        assert e.status_code == 403
    assert raised


# ---------------------------------------------------------------------------
# Route-level: webhook
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Minimal duck-typed FastAPI Request used by the webhook test
    so the test does not stand up the full ASGI runtime."""

    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    async def body(self):
        return self._body

    async def json(self):
        return json.loads(self._body.decode("utf-8"))


@pytest.mark.asyncio
async def test_webhook_rejects_when_secret_unset(monkeypatch):
    from fastapi import HTTPException
    from routes.sales_ops_procurement import procurement_bot_webhook

    monkeypatch.delenv("PROCUREMENT_BOT_WEBHOOK_SECRET", raising=False)
    db = _build_procurement_db()
    body = b'{"sourcing_request_id":"x"}'
    req = _FakeRequest(body, {"X-Procurement-Bot-Signature": "ignored"})

    raised = False
    try:
        await procurement_bot_webhook(request=req, db=db)
    except HTTPException as e:
        raised = True
        assert e.status_code == 503
        assert e.detail["code"] == "procurement_webhook_not_configured"
    assert raised


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature(monkeypatch):
    from fastapi import HTTPException
    from routes.sales_ops_procurement import procurement_bot_webhook

    monkeypatch.setenv("PROCUREMENT_BOT_WEBHOOK_SECRET", "real-secret")
    captured: list[dict] = []
    async def _fake_audit(**kwargs):
        captured.append(kwargs)
    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action", _fake_audit
    )

    db = _build_procurement_db()
    body = b'{"sourcing_request_id":"x"}'
    req = _FakeRequest(body, {"X-Procurement-Bot-Signature": "deadbeef"})

    raised = False
    try:
        await procurement_bot_webhook(request=req, db=db)
    except HTTPException as e:
        raised = True
        assert e.status_code == 401
    assert raised
    # Audit row for the rejection fired without echoing the
    # signature value.
    actions = [c["action"] for c in captured]
    assert "sales_ops.procurement.quote_webhook_rejected" in actions
    rejection = next(
        c for c in captured if c["action"] == "sales_ops.procurement.quote_webhook_rejected"
    )
    assert rejection["details"]["reason"] == "invalid_signature"
    assert "deadbeef" not in str(rejection)


@pytest.mark.asyncio
async def test_webhook_accepts_valid_signature_and_stores_quotes(monkeypatch):
    """Happy path: valid signature, known sourcing_request_id,
    quotes land in storage selected=False, sourcing request
    transitions to review_required, audit rows write."""
    from routes.sales_ops_procurement import procurement_bot_webhook
    from services.procurement_webhook_security import compute_signature

    monkeypatch.setenv("PROCUREMENT_BOT_WEBHOOK_SECRET", "real-secret")
    captured: list[dict] = []
    async def _fake_audit(**kwargs):
        captured.append(kwargs)
    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action", _fake_audit
    )

    sourcing = {
        "sourcing_request_id": "srcreq_synth",
        "opportunity_id": "opp_synth",
        "status": "sourcing_in_progress",
    }
    db = _build_procurement_db(sourcing_requests=[sourcing])

    body_payload = {
        "sourcing_request_id": "srcreq_synth",
        "bot_request_id": "bot_xyz",
        "quotes": [
            {
                "external_quote_id": "bot_q1",
                "material_line_id": "li_a",
                "vendor_name": "Synth Vendor A",
                "price": 100.0,
                "currency": "USD",
                "availability": "in_stock",
                "delivery_eta": "2099-01-15",
                "confidence": 0.9,
                "source_url": "https://vendor.example.test/sku-A",
            },
            {
                "external_quote_id": "bot_q2",
                "material_line_id": "li_a",
                "vendor_name": "Synth Vendor B",
                "price": 110.0,
                "currency": "USD",
                "availability": "in_stock",
            },
        ],
    }
    body = json.dumps(body_payload).encode("utf-8")
    sig = compute_signature(body, "real-secret")
    req = _FakeRequest(body, {"X-Procurement-Bot-Signature": sig})

    result = await procurement_bot_webhook(request=req, db=db)
    assert result["sourcing_request_id"] == "srcreq_synth"
    assert result["status"] == "review_required"
    assert len(result["stored_quote_ids"]) == 2
    # Quotes landed selected=False.
    inserted_quotes = db.procurement_quote_options.inserted
    assert len(inserted_quotes) == 2
    for q in inserted_quotes:
        assert q["selected"] is False
        assert q["sourcing_request_id"] == "srcreq_synth"
    # Sourcing request moved to review_required.
    updated_sourcing = await db.procurement_sourcing_requests.find_one(
        {"sourcing_request_id": "srcreq_synth"}
    )
    assert updated_sourcing["status"] == "review_required"
    assert updated_sourcing["manual_review_required"] is True
    # Audit rows written.
    actions = [c["action"] for c in captured]
    assert "sales_ops.procurement.quote_webhook_received" in actions
    assert actions.count("sales_ops.procurement.quote_option_stored") == 2


@pytest.mark.asyncio
async def test_webhook_idempotent_on_duplicate_external_quote_id(monkeypatch):
    """A bot retry posts the same external_quote_id again. The
    route reuses the existing quote_id and does not insert a
    duplicate row."""
    from routes.sales_ops_procurement import procurement_bot_webhook
    from services.procurement_webhook_security import compute_signature

    monkeypatch.setenv("PROCUREMENT_BOT_WEBHOOK_SECRET", "real-secret")
    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action",
        AsyncMock(return_value=None),
    )

    sourcing = {
        "sourcing_request_id": "srcreq_synth",
        "opportunity_id": "opp_synth",
        "status": "sourcing_in_progress",
    }
    existing_quote = {
        "quote_id": "quote_existing",
        "sourcing_request_id": "srcreq_synth",
        "external_quote_id": "bot_q1",
        "vendor_name": "Synth Vendor A",
        "selected": False,
    }
    db = _build_procurement_db(
        sourcing_requests=[sourcing], quote_options=[existing_quote]
    )

    body_payload = {
        "sourcing_request_id": "srcreq_synth",
        "quotes": [
            {
                "external_quote_id": "bot_q1",
                "vendor_name": "Synth Vendor A",
                "price": 100.0,
            },
        ],
    }
    body = json.dumps(body_payload).encode("utf-8")
    sig = compute_signature(body, "real-secret")
    req = _FakeRequest(body, {"X-Procurement-Bot-Signature": sig})

    result = await procurement_bot_webhook(request=req, db=db)
    assert result["stored_quote_ids"] == ["quote_existing"]
    # No new insert.
    assert db.procurement_quote_options.inserted == []


@pytest.mark.asyncio
async def test_webhook_rejects_unknown_sourcing_request(monkeypatch):
    """The bot cannot smuggle a quote into a sourcing request that
    does not exist. Tenant ownership is resolved through the
    stored sourcing request lookup, not the body."""
    from fastapi import HTTPException
    from routes.sales_ops_procurement import procurement_bot_webhook
    from services.procurement_webhook_security import compute_signature

    monkeypatch.setenv("PROCUREMENT_BOT_WEBHOOK_SECRET", "real-secret")
    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action",
        AsyncMock(return_value=None),
    )
    db = _build_procurement_db()  # no sourcing requests stored
    body_payload = {
        "sourcing_request_id": "srcreq_does_not_exist",
        "quotes": [],
    }
    body = json.dumps(body_payload).encode("utf-8")
    sig = compute_signature(body, "real-secret")
    req = _FakeRequest(body, {"X-Procurement-Bot-Signature": sig})

    raised = False
    try:
        await procurement_bot_webhook(request=req, db=db)
    except HTTPException as e:
        raised = True
        assert e.status_code == 404
    assert raised


# ---------------------------------------------------------------------------
# Route-level: select / order / deliver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_quote_stamps_selected_fields(monkeypatch):
    from routes.sales_ops_procurement import select_procurement_quote

    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action",
        AsyncMock(return_value=None),
    )
    sourcing = {
        "sourcing_request_id": "srcreq_synth",
        "opportunity_id": "opp_synth",
        "status": "review_required",
    }
    quote_a = {
        "quote_id": "quote_a",
        "sourcing_request_id": "srcreq_synth",
        "vendor_name": "Vendor A",
        "selected": False,
    }
    quote_b = {
        "quote_id": "quote_b",
        "sourcing_request_id": "srcreq_synth",
        "vendor_name": "Vendor B",
        "selected": False,
    }
    db = _build_procurement_db(
        sourcing_requests=[sourcing], quote_options=[quote_a, quote_b]
    )

    user = _user(["sales_ops.procurement.edit"])
    result = await select_procurement_quote(
        sourcing_request_id="srcreq_synth",
        quote_id="quote_a",
        db=db,
        current_user=user,
    )
    assert result["sourcing_request"]["status"] == "quote_selected"
    assert result["sourcing_request"]["selected_quote_id"] == "quote_a"
    # quote_a is selected; quote_b is not.
    fresh_a = await db.procurement_quote_options.find_one({"quote_id": "quote_a"})
    fresh_b = await db.procurement_quote_options.find_one({"quote_id": "quote_b"})
    assert fresh_a["selected"] is True
    assert fresh_a["selected_by"] == "u_synth"
    assert fresh_b["selected"] is False


@pytest.mark.asyncio
async def test_mark_ordered_requires_selected_quote(monkeypatch):
    from fastapi import HTTPException
    from routes.sales_ops_procurement import mark_procurement_ordered

    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action",
        AsyncMock(return_value=None),
    )
    sourcing = {
        "sourcing_request_id": "srcreq_synth",
        "opportunity_id": "opp_synth",
        "status": "review_required",
        "selected_quote_id": None,
    }
    db = _build_procurement_db(sourcing_requests=[sourcing])

    user = _user(["sales_ops.procurement.edit"])
    raised = False
    try:
        await mark_procurement_ordered(
            sourcing_request_id="srcreq_synth", db=db, current_user=user
        )
    except HTTPException as e:
        raised = True
        assert e.status_code == 409
        assert e.detail["code"] == "no_selected_quote"
    assert raised


@pytest.mark.asyncio
async def test_mark_ordered_stamps_and_audits(monkeypatch):
    from routes.sales_ops_procurement import mark_procurement_ordered

    captured: list[dict] = []
    async def _fake_audit(**kwargs):
        captured.append(kwargs)
    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action", _fake_audit
    )

    sourcing = {
        "sourcing_request_id": "srcreq_synth",
        "opportunity_id": "opp_synth",
        "status": "quote_selected",
        "selected_quote_id": "quote_a",
    }
    quote = {
        "quote_id": "quote_a",
        "sourcing_request_id": "srcreq_synth",
        "vendor_name": "Vendor A",
        "selected": True,
    }
    db = _build_procurement_db(
        sourcing_requests=[sourcing], quote_options=[quote]
    )
    user = _user(["sales_ops.procurement.edit"])
    fresh = await mark_procurement_ordered(
        sourcing_request_id="srcreq_synth", db=db, current_user=user
    )
    assert fresh["status"] == "ordered"
    assert fresh["ordered_by"] == "u_synth"
    actions = [c["action"] for c in captured]
    assert "sales_ops.procurement.material_ordered" in actions


@pytest.mark.asyncio
async def test_mark_delivered_requires_ordered_state(monkeypatch):
    from fastapi import HTTPException
    from routes.sales_ops_procurement import mark_procurement_delivered

    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action",
        AsyncMock(return_value=None),
    )
    sourcing = {
        "sourcing_request_id": "srcreq_synth",
        "opportunity_id": "opp_synth",
        "status": "quote_selected",
    }
    db = _build_procurement_db(sourcing_requests=[sourcing])
    user = _user(["sales_ops.procurement.edit"])
    raised = False
    try:
        await mark_procurement_delivered(
            sourcing_request_id="srcreq_synth", db=db, current_user=user
        )
    except HTTPException as e:
        raised = True
        assert e.status_code == 409
        assert e.detail["code"] == "not_ordered"
    assert raised


@pytest.mark.asyncio
async def test_mark_delivered_stamps_and_audits(monkeypatch):
    from routes.sales_ops_procurement import mark_procurement_delivered

    captured: list[dict] = []
    async def _fake_audit(**kwargs):
        captured.append(kwargs)
    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action", _fake_audit
    )

    sourcing = {
        "sourcing_request_id": "srcreq_synth",
        "opportunity_id": "opp_synth",
        "status": "ordered",
        "selected_quote_id": "quote_a",
    }
    quote = {
        "quote_id": "quote_a",
        "sourcing_request_id": "srcreq_synth",
        "vendor_name": "Vendor A",
        "selected": True,
        "ordered_at": "2099-01-01T00:00:00Z",
    }
    db = _build_procurement_db(
        sourcing_requests=[sourcing], quote_options=[quote]
    )
    user = _user(["sales_ops.procurement.edit"])
    fresh = await mark_procurement_delivered(
        sourcing_request_id="srcreq_synth", db=db, current_user=user
    )
    assert fresh["status"] == "delivered"
    assert fresh["delivered_by"] == "u_synth"
    actions = [c["action"] for c in captured]
    assert "sales_ops.procurement.material_delivered" in actions


# ---------------------------------------------------------------------------
# Anti-scope-creep
# ---------------------------------------------------------------------------


def test_no_real_vendor_names_in_scaffold():
    """The scaffold must not hardcode real vendor names. The list
    below seeds a few common supplier names; if any future commit
    drops one of these into backend code, the test fails loudly so
    the maintainer either deletes the name or adds a separate
    docs-only entry in the vendor matrix template. The matrix
    template is the canonical home for vendor names; route code
    is not."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    route_path = repo_root / "backend" / "routes" / "sales_ops_procurement.py"
    if not route_path.exists():
        pytest.skip("backend not mounted alongside tests")
    text = route_path.read_text(encoding="utf-8").lower()
    forbidden_real_vendor_names = [
        "amazon",
        "grainger",
        "graybar",
        "anixter",
        "wesco",
        "cdw",
        "insight",
    ]
    for name in forbidden_real_vendor_names:
        assert name not in text, (
            f"real vendor name {name!r} must not appear in backend route code"
        )


def test_scaffold_doc_exists_and_names_audit_actions():
    """The scaffold doc names every audit action introduced by this
    ticket. Skips cleanly when docs are not mounted."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    doc_path = repo_root / "docs" / "sales-ops" / "PROCUREMENT_BOT_SCAFFOLD.md"
    if not doc_path.exists():
        pytest.skip("scaffold doc not mounted alongside backend tests")
    text = doc_path.read_text(encoding="utf-8")
    for action in (
        "sales_ops.procurement.sourcing_requested",
        "sales_ops.procurement.bot_submission_attempted",
        "sales_ops.procurement.bot_submission_failed",
        "sales_ops.procurement.sourcing_retry_requested",
        "sales_ops.procurement.quote_webhook_received",
        "sales_ops.procurement.quote_webhook_rejected",
        "sales_ops.procurement.quote_option_stored",
        "sales_ops.procurement.quote_selected",
        "sales_ops.procurement.material_ordered",
        "sales_ops.procurement.material_delivered",
    ):
        assert action in text, f"scaffold doc missing audit action {action!r}"


def test_scaffold_doc_documents_mode_b_default():
    """The doc explicitly states that every quote arrives
    selected=False and that the route never auto-selects."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    doc_path = repo_root / "docs" / "sales-ops" / "PROCUREMENT_BOT_SCAFFOLD.md"
    if not doc_path.exists():
        pytest.skip("scaffold doc not mounted alongside backend tests")
    text = doc_path.read_text(encoding="utf-8").lower()
    assert "mode b" in text
    assert "never auto-selects" in text or "never auto-select" in text or "manual-review-only" in text
    assert "selected=false" in text or "selected=false" in text.replace(" ", "")


# ---------------------------------------------------------------------------
# Code-review follow-ups: list / detail / retry routes, never-raises
# bot client contract, no-auth-in-result, mark-ordered status gate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sourcing_requests_filters_by_status():
    from routes.sales_ops_procurement import list_procurement_sourcing_requests

    docs = [
        {"sourcing_request_id": "srcreq_a", "opportunity_id": "opp_a", "status": "review_required", "created_at": "2099-01-01T00:00:00Z"},
        {"sourcing_request_id": "srcreq_b", "opportunity_id": "opp_b", "status": "ordered", "created_at": "2099-01-02T00:00:00Z"},
        {"sourcing_request_id": "srcreq_c", "opportunity_id": "opp_a", "status": "review_required", "created_at": "2099-01-03T00:00:00Z"},
    ]
    db = SimpleNamespace(procurement_sourcing_requests=_FakeCollection(docs))
    user = _user(["sales_ops.procurement.view"])
    result = await list_procurement_sourcing_requests(
        opportunity_id=None, status="review_required", db=db, current_user=user
    )
    ids = {r["sourcing_request_id"] for r in result["items"]}
    assert ids == {"srcreq_a", "srcreq_c"}


@pytest.mark.asyncio
async def test_list_sourcing_requests_filters_by_opportunity():
    from routes.sales_ops_procurement import list_procurement_sourcing_requests

    docs = [
        {"sourcing_request_id": "srcreq_a", "opportunity_id": "opp_a", "status": "review_required"},
        {"sourcing_request_id": "srcreq_b", "opportunity_id": "opp_b", "status": "review_required"},
    ]
    db = SimpleNamespace(procurement_sourcing_requests=_FakeCollection(docs))
    user = _user(["sales_ops.procurement.view"])
    result = await list_procurement_sourcing_requests(
        opportunity_id="opp_a", status=None, db=db, current_user=user
    )
    ids = {r["sourcing_request_id"] for r in result["items"]}
    assert ids == {"srcreq_a"}


@pytest.mark.asyncio
async def test_list_sourcing_requests_requires_view_permission():
    from fastapi import HTTPException
    from routes.sales_ops_procurement import list_procurement_sourcing_requests

    db = SimpleNamespace(procurement_sourcing_requests=_FakeCollection([]))
    user = _user([])
    raised = False
    try:
        await list_procurement_sourcing_requests(
            opportunity_id=None, status=None, db=db, current_user=user
        )
    except HTTPException as e:
        raised = True
        assert e.status_code == 403
    assert raised


@pytest.mark.asyncio
async def test_get_sourcing_request_returns_request_and_quotes():
    from routes.sales_ops_procurement import get_procurement_sourcing_request

    sourcing = {
        "sourcing_request_id": "srcreq_synth",
        "opportunity_id": "opp_synth",
        "status": "review_required",
    }
    quote = {
        "quote_id": "quote_a",
        "sourcing_request_id": "srcreq_synth",
        "vendor_name": "Vendor A",
        "selected": False,
        "created_at": "2099-01-01T00:00:00Z",
    }
    db = _build_procurement_db(
        sourcing_requests=[sourcing], quote_options=[quote]
    )
    user = _user(["sales_ops.procurement.view"])
    result = await get_procurement_sourcing_request(
        sourcing_request_id="srcreq_synth", db=db, current_user=user
    )
    assert result["sourcing_request"]["sourcing_request_id"] == "srcreq_synth"
    assert len(result["quotes"]) == 1
    assert result["quotes"][0]["quote_id"] == "quote_a"


@pytest.mark.asyncio
async def test_get_sourcing_request_404_when_unknown():
    from fastapi import HTTPException
    from routes.sales_ops_procurement import get_procurement_sourcing_request

    db = _build_procurement_db()
    user = _user(["sales_ops.procurement.view"])
    raised = False
    try:
        await get_procurement_sourcing_request(
            sourcing_request_id="srcreq_unknown", db=db, current_user=user
        )
    except HTTPException as e:
        raised = True
        assert e.status_code == 404
    assert raised


@pytest.mark.asyncio
async def test_retry_returns_503_when_bot_unconfigured(monkeypatch):
    """Retry against an unconfigured bot returns a structured
    procurement_bot_not_configured envelope rather than retrying
    indefinitely. The route directs the user to the manual queue."""
    from fastapi import HTTPException
    from routes.sales_ops_procurement import retry_procurement_sourcing_request

    monkeypatch.delenv("PROCUREMENT_BOT_BASE_URL", raising=False)
    monkeypatch.delenv("PROCUREMENT_BOT_API_KEY", raising=False)
    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action",
        AsyncMock(return_value=None),
    )

    sourcing = {
        "sourcing_request_id": "srcreq_synth",
        "opportunity_id": "opp_synth",
        "status": "failed",
        "line_item_ids": [],
    }
    db = _build_procurement_db(sourcing_requests=[sourcing])
    user = _user(["sales_ops.procurement.edit"])

    raised = False
    try:
        await retry_procurement_sourcing_request(
            sourcing_request_id="srcreq_synth", db=db, current_user=user
        )
    except HTTPException as e:
        raised = True
        assert e.status_code == 503
        assert e.detail["code"] == "procurement_bot_not_configured"
    assert raised


@pytest.mark.asyncio
async def test_retry_rejects_terminal_status(monkeypatch):
    """Retry refuses when the request is already in a terminal
    status (delivered or cancelled). The 409 carries a structured
    code so the frontend can route to a meaningful message."""
    from fastapi import HTTPException
    from routes.sales_ops_procurement import retry_procurement_sourcing_request

    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action",
        AsyncMock(return_value=None),
    )

    sourcing = {
        "sourcing_request_id": "srcreq_synth",
        "opportunity_id": "opp_synth",
        "status": "delivered",
    }
    db = _build_procurement_db(sourcing_requests=[sourcing])
    user = _user(["sales_ops.procurement.edit"])

    raised = False
    try:
        await retry_procurement_sourcing_request(
            sourcing_request_id="srcreq_synth", db=db, current_user=user
        )
    except HTTPException as e:
        raised = True
        assert e.status_code == 409
        assert e.detail["code"] == "sourcing_retry_not_allowed"
    assert raised


@pytest.mark.asyncio
async def test_mark_ordered_refuses_review_required_status(monkeypatch):
    """Mark-ordered requires the explicit quote_selected status.
    A review_required request that somehow has selected_quote_id
    set (e.g. via a direct DB write) still cannot land in ordered."""
    from fastapi import HTTPException
    from routes.sales_ops_procurement import mark_procurement_ordered

    monkeypatch.setattr(
        "routes.sales_ops_procurement.audit_log_action",
        AsyncMock(return_value=None),
    )

    sourcing = {
        "sourcing_request_id": "srcreq_synth",
        "opportunity_id": "opp_synth",
        "status": "review_required",
        "selected_quote_id": "quote_a",
    }
    db = _build_procurement_db(sourcing_requests=[sourcing])
    user = _user(["sales_ops.procurement.edit"])

    raised = False
    try:
        await mark_procurement_ordered(
            sourcing_request_id="srcreq_synth", db=db, current_user=user
        )
    except HTTPException as e:
        raised = True
        assert e.status_code == 409
        assert e.detail["code"] == "sourcing_not_quote_selected"
    assert raised


# ---------------------------------------------------------------------------
# Bot client safety contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bot_client_never_raises_on_unexpected_exception(monkeypatch):
    """The bot client contract is 'never raises'. An unexpected
    non-httpx exception inside the post call collapses to a
    transport_error result so the route can land in the manual
    review queue."""
    from services.procurement_bot_client import submit_sourcing_request

    monkeypatch.setenv("PROCUREMENT_BOT_BASE_URL", "https://bot.example.test")
    monkeypatch.setenv("PROCUREMENT_BOT_API_KEY", "test-key")

    class _BrokenClient:
        async def post(self, *_args, **_kwargs):
            raise RuntimeError("synthetic non-http error")

        async def aclose(self):
            return None

    client = _BrokenClient()
    result = await submit_sourcing_request({"sourcing_request_id": "x"}, client=client)
    assert result.configured is True
    assert result.accepted is False
    assert result.error_reason == "transport_error"


@pytest.mark.asyncio
async def test_bot_client_result_does_not_include_authorization_value(monkeypatch):
    """The Authorization header is sent to the bot but the API key
    value never appears in any field of BotSubmissionResult. A
    careless future refactor that echoes the header into the
    result will fail this test."""
    from services.procurement_bot_client import submit_sourcing_request

    monkeypatch.setenv("PROCUREMENT_BOT_BASE_URL", "https://bot.example.test")
    monkeypatch.setenv("PROCUREMENT_BOT_API_KEY", "super-secret-key-789")

    transport = httpx.MockTransport(
        lambda _r: httpx.Response(200, json={"bot_request_id": "bot_abc"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await submit_sourcing_request(
            {"sourcing_request_id": "x"}, client=client
        )
    rendered = repr(result)
    assert "super-secret-key-789" not in rendered
    assert "Bearer" not in rendered


@pytest.mark.asyncio
async def test_bot_client_returns_unconfigured_when_env_emptied_after_check(monkeypatch):
    """Race-condition contract: if the env var is removed between
    the is_bot_configured check and the actual read, the client
    must collapse to bot_not_configured rather than raise."""
    from services.procurement_bot_client import submit_sourcing_request

    monkeypatch.setenv("PROCUREMENT_BOT_BASE_URL", "https://bot.example.test")
    monkeypatch.setenv("PROCUREMENT_BOT_API_KEY", "test-key")

    # Simulate the race by patching is_bot_configured to return
    # True while leaving env values that would still satisfy the
    # internal read; then re-confirm the path also handles env
    # being scrubbed (defensive double-read).
    monkeypatch.setenv("PROCUREMENT_BOT_BASE_URL", "")
    monkeypatch.setenv("PROCUREMENT_BOT_API_KEY", "")

    import services.procurement_bot_client as mod
    monkeypatch.setattr(mod, "is_bot_configured", lambda: True)

    result = await submit_sourcing_request({"sourcing_request_id": "x"})
    assert result.accepted is False
    assert result.configured is False
    assert result.error_reason == "bot_not_configured"


# ---------------------------------------------------------------------------
# Foundation contract locks — env placeholders + route mounting
# ---------------------------------------------------------------------------


def test_env_example_carries_procurement_bot_placeholders():
    """Operational deploys configure the bot via env. The example
    file must enumerate the canonical names so an operator copying
    `.env.example` -> `.env` and filling values does not have to
    grep the code to discover them. Each placeholder is intentionally
    left blank — the bot stays in Mode B (manual-review) until the
    operator opts in by populating both BASE_URL and API_KEY (and the
    WEBHOOK_SECRET for inbound callbacks)."""
    from pathlib import Path

    env_example = (
        Path(__file__).resolve().parents[1] / ".env.example"
    )
    if not env_example.exists():  # pragma: no cover
        pytest.skip(".env.example not mounted in this test image")
    src = env_example.read_text(encoding="utf-8")
    # All three canonical names must be present so deploy copy-paste
    # cannot miss them.
    for name in (
        "PROCUREMENT_BOT_BASE_URL=",
        "PROCUREMENT_BOT_API_KEY=",
        "PROCUREMENT_BOT_WEBHOOK_SECRET=",
        "PROCUREMENT_BOT_TIMEOUT_SECONDS=",
    ):
        assert name in src, (
            f".env.example must declare {name!r} so operators see the "
            "canonical procurement-bot env name without grepping code."
        )
    # The example MUST NOT carry a real secret or example key. Each
    # placeholder is either blank or a sentinel — never an actual key.
    suspicious_tokens = (
        "sk_live_",
        "sk_test_",
        "AKIA",  # AWS access key id pattern
        "AIza",  # google api key prefix
    )
    for token in suspicious_tokens:
        assert token not in src, (
            f"{token!r} looks like a real credential. The .env.example "
            "must only carry blank or sentinel placeholders."
        )


def test_procurement_router_is_mounted_in_server():
    """Source-level lock that the procurement router (which exposes
    the bot webhook + the AEGISAXIOM-side sourcing-request CRUD) is
    actually wired into server.py. Without this lock a future refactor
    that splits routers could drop the include and the webhook URL
    would silently 404."""
    from pathlib import Path

    server_py = (
        Path(__file__).resolve().parents[1] / "server.py"
    )
    if not server_py.exists():  # pragma: no cover
        pytest.skip("server.py not mounted in this test image")
    src = server_py.read_text(encoding="utf-8")
    assert (
        "from routes.sales_ops_procurement import router as sales_ops_procurement_router"
        in src
    ), "server.py must import the procurement router."
    assert "sales_ops_procurement_router" in src, (
        "server.py must include the procurement router."
    )
    # Mounted under /sales-ops prefix so the webhook URL stabilizes
    # at /api/sales-ops/procurement/bot/webhook.
    assert (
        'include_router(sales_ops_procurement_router, prefix="/sales-ops"'
        in src
    ), (
        "Procurement router must be mounted with prefix=\"/sales-ops\" "
        "so the webhook URL stabilizes at "
        "/api/sales-ops/procurement/bot/webhook for the bot to call."
    )


def test_webhook_signature_helper_uses_shared_secret_env():
    """The webhook signature helper resolves the shared secret from
    PROCUREMENT_BOT_WEBHOOK_SECRET. Locking the env name keeps an
    operator's .env.example match with what the code actually reads."""
    from services.procurement_webhook_security import SECRET_ENV_VAR

    assert SECRET_ENV_VAR == "PROCUREMENT_BOT_WEBHOOK_SECRET", (
        "Webhook signature helper must read from "
        "PROCUREMENT_BOT_WEBHOOK_SECRET; renaming this var would "
        "break operational deploys without a migration path."
    )


def test_bot_client_env_var_names_are_stable():
    """Lock the env var names the bot client reads at call time so
    deploys do not drift from the documented .env.example. Two
    distinct secrets by design: BASE_URL + API_KEY for outbound,
    WEBHOOK_SECRET for inbound — never collapsed into one."""
    from services.procurement_bot_client import (
        BOT_BASE_URL_ENV,
        BOT_API_KEY_ENV,
        BOT_TIMEOUT_ENV,
    )

    assert BOT_BASE_URL_ENV == "PROCUREMENT_BOT_BASE_URL"
    assert BOT_API_KEY_ENV == "PROCUREMENT_BOT_API_KEY"
    assert BOT_TIMEOUT_ENV == "PROCUREMENT_BOT_TIMEOUT_SECONDS"
