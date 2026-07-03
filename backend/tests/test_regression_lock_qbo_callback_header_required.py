"""Regression lock. The n8n callback endpoint must require the
X-AEGISAXIOM-Sync-Request-Id routing header before signature
verification. The lock asserts the same 400 surfaces no matter
what status value the body carries so an attacker who lifted a
valid signature for one record cannot replay it against a
different record by dropping the header.

Commit A added the header presence guard in routes/qbo_sync.py
and one happy path test. This file is the Commit C regression
lock counterpart. It parametrizes the body status field across
success, failure, and partial so a future refactor that
accidentally skips the guard for some status code is caught.

Synthetic only. The body signature is intentionally bogus because
the guard fires before signature verification.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest")

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient


_FAKE_INBOUND_SECRET = "fake-inbound-secret-not-real"


def _make_user():
    return SimpleNamespace(
        user_id="u_synth_c_cb",
        email="u_synth_c_cb@example.invalid",
        role="finance_admin",
        permissions=["finance.sync.trigger", "finance.meta.view"],
        tenant_id=None,
    )


class _FakeSyncCollection:
    """Tiny stub so the route's db lookups do not blow up. The header
    guard fires before any db read but the patches still need a
    coherent target so request setup does not crash on a None db."""

    async def find_one(self, *args, **kwargs):
        return None

    async def update_one(self, *args, **kwargs):
        return MagicMock(matched_count=1, modified_count=1)

    async def insert_one(self, *args, **kwargs):
        return MagicMock(inserted_id="fake_oid")


class _FakeDb:
    def __init__(self):
        self.qbo_sync_requests = _FakeSyncCollection()
        self.audit_logs = MagicMock()
        self.audit_logs.insert_one = AsyncMock(return_value=MagicMock())

    def __getitem__(self, key):
        return getattr(self, key)


@pytest.fixture
def callback_app(monkeypatch):
    """Mount the qbo_sync router with auth and db overrides. Set the
    inbound signing secret so signature verification can be reached
    only if the header guard is bypassed. The test deliberately omits
    the header so the guard MUST fire first."""
    monkeypatch.setenv("AEGISAXIOM_QBO_SYNC_CALLBACK_SECRET", _FAKE_INBOUND_SECRET)
    monkeypatch.setenv("QBO_SYNC_TRIGGER_ENABLED", "true")
    from routes.qbo_sync import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: _make_user()
    db = _FakeDb()
    with patch("routes.qbo_sync.get_db", return_value=db), \
         patch("services.audit_service.get_db", return_value=db):
        yield app
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "status_value",
    [
        pytest.param("complete", id="success_status"),
        pytest.param("failed", id="failure_status"),
        pytest.param("partial", id="partial_status"),
    ],
)
def test_callback_without_header_rejected_400_across_status_values(
    callback_app, status_value,
):
    """Whatever status the body carries the missing header rejects
    with 400 before signature verification runs. A 401 here would
    indicate the guard ran AFTER signature verification which is
    the pre fix order and lets a forged body get hashed before the
    routing check rejects it. A 4xx other than 400 would also break
    the contract."""
    body = json.dumps(
        {
            "sync_request_id": "synth_req_c_" + status_value,
            "status": status_value,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        # X-N8N-Signature carries a deliberately bogus value. The
        # header guard must fire before this is parsed.
        "X-N8N-Signature": "sha256=00",
        # X-AEGISAXIOM-Sync-Request-Id is intentionally omitted.
    }
    client = TestClient(callback_app)
    resp = client.post(
        "/api/qbo/sync/callback", content=body, headers=headers,
    )
    assert resp.status_code == 400, (
        "regression. expected 400 for missing header on status "
        + status_value + " got " + str(resp.status_code) + " "
        + resp.text
    )
    detail = resp.json().get("detail", "")
    assert "X-AEGISAXIOM-Sync-Request-Id" in detail, (
        "400 detail must name the missing header for operator "
        + "diagnosis got " + repr(detail)
    )


def test_callback_with_empty_header_value_treated_as_missing(callback_app):
    """An empty string header value is treated identically to a
    missing header. Without this the contract is gameable by sending
    the header with no value to bypass the guard."""
    body = json.dumps(
        {"sync_request_id": "synth_req_c_empty", "status": "complete"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-N8N-Signature": "sha256=00",
        "X-AEGISAXIOM-Sync-Request-Id": "",
    }
    client = TestClient(callback_app)
    resp = client.post(
        "/api/qbo/sync/callback", content=body, headers=headers,
    )
    assert resp.status_code == 400, (
        "empty header value must reject 400 got "
        + str(resp.status_code) + " " + resp.text
    )
