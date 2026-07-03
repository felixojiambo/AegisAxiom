"""Locks the QBO sync trigger request date range coherence rule.

The TriggerRequest model rejects an inverted date range where
``date_from`` is strictly after ``date_to``. The error message is
fixed and does not echo either input value so an LLM cannot reflect
arbitrary content through the response. Equal dates are accepted
as a one day window. The route surfaces the validation failure as
HTTP 422 per the FastAPI default.

Synthetic only. Far future dates so the case is stable.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest")

import pytest


def test_trigger_request_rejects_inverted_date_range():
    """date_from strictly after date_to must raise on construction.
    The error message is fixed and does not echo either input value
    so an LLM cannot reflect arbitrary content through the response."""
    from pydantic import ValidationError
    from routes.qbo_sync import TriggerRequest

    with pytest.raises(ValidationError) as exc_info:
        TriggerRequest(
            sources=["gl"],
            date_from="2099-01-01",
            date_to="2098-01-01",
        )
    rendered = str(exc_info.value)
    assert "date_from must be on or before date_to" in rendered
    assert "2099-01-01" not in rendered.split("\n")[0]
    assert "2098-01-01" not in rendered.split("\n")[0]


def test_trigger_request_accepts_equal_dates():
    """date_from equal to date_to is a valid one day window."""
    from routes.qbo_sync import TriggerRequest

    body = TriggerRequest(
        sources=["gl"],
        date_from="2099-04-01",
        date_to="2099-04-01",
    )
    assert body.date_from == body.date_to


def test_trigger_request_accepts_valid_range():
    from routes.qbo_sync import TriggerRequest

    body = TriggerRequest(
        sources=["gl"],
        date_from="2099-04-01",
        date_to="2099-05-01",
    )
    assert body.date_from < body.date_to


def test_trigger_request_route_returns_422_for_inverted_range(monkeypatch):
    """End to end the inverted range surfaces as HTTP 422 from the
    trigger endpoint because pydantic ValidationError on a request
    body produces a 422 in FastAPI by default."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("N8N_QBO_SYNC_WEBHOOK_URL", "https://n8n.example.test/cb")
    monkeypatch.setenv("N8N_QBO_SYNC_SECRET", "fake-outbound")
    monkeypatch.setenv("AEGISAXIOM_QBO_SYNC_CALLBACK_SECRET", "fake-inbound")
    monkeypatch.setenv("AEGISAXIOM_PUBLIC_API_BASE_URL", "https://api.aegisaxiom.example.test")
    monkeypatch.setenv("QBO_SYNC_TRIGGER_ENABLED", "true")

    from routes.qbo_sync import router
    from auth_helper import get_current_user

    user = SimpleNamespace(
        user_id="u_synth_d3",
        email="u_synth_d3@example.invalid",
        role="finance_admin",
        permissions=["finance.sync.trigger", "finance.meta.view"],
    )
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: user

    db = MagicMock()
    db.qbo_sync_requests = MagicMock()
    db.qbo_sync_requests.find_one = AsyncMock(return_value=None)
    db.qbo_sync_requests.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))
    db.audit_logs = MagicMock()
    db.audit_logs.insert_one = AsyncMock(return_value=MagicMock(inserted_id="y"))
    with patch("routes.qbo_sync.get_db", return_value=db), \
         patch("services.audit_service.get_db", return_value=db):
        client = TestClient(app)
        resp = client.post(
            "/api/qbo/sync/trigger",
            json={
                "sources": ["gl"],
                "date_from": "2099-01-01",
                "date_to": "2098-01-01",
            },
        )
    assert resp.status_code == 422
    body_text = resp.text
    assert "date_from must be on or before date_to" in body_text
