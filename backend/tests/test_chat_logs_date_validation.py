"""Unit tests for the get_chat_logs admin endpoint date parsing.

PR cleanup A4. The endpoint parses date_from and date_to via
datetime.fromisoformat. The earlier path had no try except so a
malformed value returned 500 to the caller. The fix wraps both
parses and surfaces HTTP 400 with a fixed detail string.

The test builds a minimal FastAPI app that includes only the chat
router with overrides for get_current_user and get_db. Importing
the full server module is intentionally avoided so the unit test
does not depend on optional runtime modules pulled in by routes
unrelated to the chat logs endpoint.

Synthetic only. No production tenant data, no live tokens, all
state lives behind FastAPI TestClient.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest")

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_admin():
    return SimpleNamespace(
        user_id="u_synth_a4",
        email="u_synth_a4@example.invalid",
        role="admin",
        permissions=["*"],
        tenant_id=None,
    )


def _make_mock_db():
    db = MagicMock()
    db.chat_sessions = MagicMock()
    db.chat_sessions.count_documents = AsyncMock(return_value=0)
    cursor = MagicMock()
    cursor.sort.return_value.skip.return_value.limit.return_value.to_list = (
        AsyncMock(return_value=[])
    )
    db.chat_sessions.find = MagicMock(return_value=cursor)
    db.chat_messages = MagicMock()
    db.chat_messages.count_documents = AsyncMock(return_value=0)
    return db


@pytest.fixture
def admin_app():
    """Build a fresh FastAPI app with just the chat router and the
    minimum auth and db overrides needed for the logs endpoint.

    The chat route calls get_db as a plain function rather than as a
    FastAPI dependency so we patch the module level reference for
    the test lifetime instead of using app.dependency_overrides."""
    from routes.chat import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/chat")
    admin = _make_admin()
    mock_db = _make_mock_db()
    app.dependency_overrides[get_current_user] = lambda: admin
    with patch("routes.chat.get_db", return_value=mock_db):
        yield app
    app.dependency_overrides.clear()


def test_get_chat_logs_returns_400_on_malformed_date_from(admin_app):
    """date_from=not-a-date must return 400 not 500. The fix wraps
    the fromisoformat call so a ValueError surfaces as a controlled
    HTTPException."""
    client = TestClient(admin_app, raise_server_exceptions=False)
    resp = client.get("/api/chat/admin/logs?date_from=not-a-date")
    assert resp.status_code == 400, (
        "expected 400 for malformed date_from got "
        + str(resp.status_code) + " " + resp.text
    )
    body = resp.json()
    assert "invalid date format" in body.get("detail", "").lower()


def test_get_chat_logs_returns_400_on_malformed_date_to(admin_app):
    """date_to with bad value also surfaces 400."""
    client = TestClient(admin_app, raise_server_exceptions=False)
    resp = client.get("/api/chat/admin/logs?date_to=garbage")
    assert resp.status_code == 400, (
        "expected 400 for malformed date_to got "
        + str(resp.status_code) + " " + resp.text
    )
