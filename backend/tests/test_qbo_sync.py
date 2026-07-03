"""Tests for the AEGISAXIOM-side QBO sync orchestration.

n8n owns QBO ingestion. AEGISAXIOM only triggers, signs, verifies, persists,
audits, and serves a freshness signal. These tests exercise the
trigger / callback / status endpoints, the HMAC layer, the in-memory
sync_request lifecycle, and the Nova freshness helper.

Pattern mirrors test_finance_coa_mapping.py (canonical AEGISAXIOM async test
style — FastAPI TestClient + AsyncMock + dependency overrides; no
live Mongo). All fixtures are synthetic — no production URLs, secrets,
realm IDs, or company names.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import qbo_sync_service as svc


# ---------------------------------------------------------------------------
# Synthetic config — applied to os.environ for tests that need the
# trigger/callback path to read the values via the env-var helpers.
# Keep these obviously-fake so a stray hardcode would jump out at us.
# ---------------------------------------------------------------------------
_FAKE_N8N_URL = "https://n8n.example.test/webhook/qbosync-fake"
_FAKE_OUTBOUND_SECRET = "fake-outbound-secret-not-real"
_FAKE_INBOUND_SECRET = "fake-inbound-secret-not-real"
_FAKE_AEGISAXIOM_BASE = "https://api.aegisaxiom.example.test"


@pytest.fixture
def env_configured(monkeypatch):
    """Set the env vars the trigger / callback path requires."""
    monkeypatch.setenv("N8N_QBO_SYNC_WEBHOOK_URL", _FAKE_N8N_URL)
    monkeypatch.setenv("N8N_QBO_SYNC_SECRET", _FAKE_OUTBOUND_SECRET)
    monkeypatch.setenv("AEGISAXIOM_QBO_SYNC_CALLBACK_SECRET", _FAKE_INBOUND_SECRET)
    monkeypatch.setenv("AEGISAXIOM_PUBLIC_API_BASE_URL", _FAKE_AEGISAXIOM_BASE)
    monkeypatch.setenv("QBO_SYNC_TRIGGER_ENABLED", "true")
    monkeypatch.setenv("QBO_SYNC_STALE_AFTER_SECONDS", "3600")
    yield


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _make_user(user_id="u_001", email="tester@aegisaxiom.test", permissions=None):
    perms = permissions if permissions is not None else [
        "finance.sync.trigger",
        "finance.meta.view",
    ]
    return SimpleNamespace(
        user_id=user_id,
        email=email,
        role="finance_admin",
        permissions=perms,
    )


def _make_user_dict(**kw):
    """`has_perm` accepts dicts; tests use the dict form to prove that
    the route normalises whatever shape the auth layer returns."""
    base = {
        "user_id": kw.get("user_id", "u_001"),
        "email": kw.get("email", "tester@aegisaxiom.test"),
        "permissions": kw.get(
            "permissions", ["finance.sync.trigger", "finance.meta.view"]
        ),
        "role": kw.get("role", "finance_admin"),
    }
    return base


class _FakeSyncCollection:
    """In-memory replacement for ``db.qbo_sync_requests``.

    Stores records by sync_request_id. Implements the subset of motor
    collection methods routes/qbo_sync.py and qbo_sync_service touch:
    insert_one, find_one (with sort kwarg), update_one ($set).
    """

    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def insert_one(self, record: dict) -> Any:
        # Defensive copy so callers mutating the input don't leak in.
        self.docs.append(dict(record))
        return MagicMock(inserted_id="fake_oid")

    def _matches(self, doc: dict, query: dict) -> bool:
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

    async def find_one(self, query: dict, projection: Optional[dict] = None,
                       sort: Optional[list] = None) -> Optional[dict]:
        matching = [d for d in self.docs if self._matches(d, query)]
        if sort:
            field, direction = sort[0]
            matching.sort(
                key=lambda d: (d.get(field) is None, d.get(field)),
                reverse=(direction == -1),
            )
        if not matching:
            return None
        # Strip _id like the real driver would when projection excludes it.
        doc = dict(matching[0])
        doc.pop("_id", None)
        return doc

    async def update_one(self, query: dict, update: dict) -> Any:
        for d in self.docs:
            if self._matches(d, query):
                d.update(update.get("$set") or {})
                return MagicMock(matched_count=1, modified_count=1)
        return MagicMock(matched_count=0, modified_count=0)


class _FakeAuditLogs:
    """Captures audit_logs.insert_one calls so tests can assert that
    qbo.sync.* events were emitted with the right shape."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    async def insert_one(self, doc: dict) -> Any:
        self.entries.append(dict(doc))
        return MagicMock(inserted_id="fake_audit")


class _FakeDb:
    """db['qbo_sync_requests'] / db.audit_logs."""

    def __init__(self) -> None:
        self.qbo_sync_requests = _FakeSyncCollection()
        self.audit_logs = _FakeAuditLogs()

    def __getitem__(self, key):
        return getattr(self, key)

    def __getattr__(self, key):
        # Auto-vivify any other collection name as a no-op mock so a
        # stray reference doesn't crash a test that doesn't care.
        if key.startswith("_"):
            raise AttributeError(key)
        coll = MagicMock()
        coll.find_one = AsyncMock(return_value=None)
        coll.insert_one = AsyncMock(return_value=MagicMock())
        return coll


def _build_app(fake_user, fake_db):
    from routes.qbo_sync import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return app, fake_db


def _patch_db(fake_db):
    """Patch every place the routes / services read get_db() so the
    fake collection is used end-to-end. ``services.audit_service.get_db``
    must be patched too — audit_service does its own get_db() call."""
    return [
        patch("routes.qbo_sync.get_db", return_value=fake_db),
        patch("services.audit_service.get_db", return_value=fake_db),
    ]


def _enter_all(patches):
    started = []
    try:
        for p in patches:
            started.append(p.__enter__())
        return started, patches
    except Exception:
        for p in patches[: len(started)]:
            p.__exit__(None, None, None)
        raise


def _exit_all(patches):
    for p in patches:
        p.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Phase 1 — HMAC helpers (unit)
# ---------------------------------------------------------------------------


class TestHmacHelpers:
    def test_sign_and_verify_round_trip(self, env_configured):
        body = {"sync_request_id": "qbosync_x", "status": "running"}
        sig, raw = svc.sign_outbound_payload(body)
        assert sig.startswith("sha256=")
        assert isinstance(raw, bytes)
        # sign_outbound uses N8N_QBO_SYNC_SECRET; verify uses
        # AEGISAXIOM_QBO_SYNC_CALLBACK_SECRET. Use the inbound secret to
        # produce the inbound digest the verifier expects.
        inbound_digest = hmac.new(
            _FAKE_INBOUND_SECRET.encode(), raw, hashlib.sha256
        ).hexdigest()
        svc.verify_callback_signature(raw, f"sha256={inbound_digest}")

    def test_verify_rejects_missing_signature(self, env_configured):
        with pytest.raises(svc.QboSyncSignatureError):
            svc.verify_callback_signature(b"{}", None)

    def test_verify_rejects_wrong_scheme(self, env_configured):
        with pytest.raises(svc.QboSyncSignatureError):
            svc.verify_callback_signature(b"{}", "md5=abcdef")

    def test_verify_rejects_mismatch(self, env_configured):
        with pytest.raises(svc.QboSyncSignatureError):
            svc.verify_callback_signature(b'{"a":1}', "sha256=deadbeef")

    def test_secrets_distinct(self, env_configured):
        # If a leaked outbound secret could verify inbound, the
        # round-trip below would succeed.
        body = {"x": 1}
        _, raw = svc.sign_outbound_payload(body)
        outbound_digest = hmac.new(
            _FAKE_OUTBOUND_SECRET.encode(), raw, hashlib.sha256
        ).hexdigest()
        with pytest.raises(svc.QboSyncSignatureError):
            svc.verify_callback_signature(raw, f"sha256={outbound_digest}")


# ---------------------------------------------------------------------------
# Phase 2 — trigger endpoint
# ---------------------------------------------------------------------------


class TestTriggerEndpoint:
    def _post_trigger(self, fake_user, fake_db, body, n8n_response=None):
        """Helper that wires the route, fake DB, audit, and a stub
        httpx.AsyncClient that returns the supplied n8n_response.

        Returns (response, fake_db) so tests can assert on side effects.
        """
        app, _ = _build_app(fake_user, fake_db)

        async def _fake_post(self, url, content=None, headers=None):
            self.last_url = url
            self.last_content = content
            self.last_headers = headers
            if n8n_response is None:
                return MagicMock(
                    status_code=200,
                    json=lambda: {"accepted": True, "n8n_execution_id": "exec_abc"},
                )
            return n8n_response

        async def _fake_aclose(self):
            return None

        patches = _patch_db(fake_db) + [
            patch.object(httpx.AsyncClient, "post", _fake_post),
            patch.object(httpx.AsyncClient, "aclose", _fake_aclose),
        ]
        started, opened = _enter_all(patches)
        try:
            client = TestClient(app)
            resp = client.post("/api/qbo/sync/trigger", json=body)
            return resp
        finally:
            _exit_all(opened)

    def test_trigger_requires_finance_sync_perm(self, env_configured):
        # User without finance.sync.trigger -> 403.
        user = _make_user(permissions=["finance.meta.view"])
        db = _FakeDb()
        resp = self._post_trigger(user, db, {"sources": ["gl"]})
        assert resp.status_code == 403
        assert "finance.sync.trigger" in resp.json()["detail"]

    def test_trigger_requires_authenticated_user(self, env_configured):
        # Override the auth dep to simulate the unauthenticated path —
        # i.e. raise the same 401 the real auth_helper would for a
        # missing session/Bearer header. Bypassing auth_helper's
        # internal db lookup keeps this test independent of the real
        # database.init() singleton.
        from fastapi import HTTPException
        from routes.qbo_sync import router
        from auth_helper import get_current_user

        async def _no_auth():
            raise HTTPException(status_code=401, detail="Not authenticated")

        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_current_user] = _no_auth

        patches = _patch_db(_FakeDb())
        started, opened = _enter_all(patches)
        try:
            client = TestClient(app)
            resp = client.post("/api/qbo/sync/trigger", json={"sources": ["gl"]})
            assert resp.status_code == 401
        finally:
            _exit_all(opened)

    def test_trigger_disabled_by_config_returns_503(self, env_configured, monkeypatch):
        monkeypatch.setenv("QBO_SYNC_TRIGGER_ENABLED", "false")
        user = _make_user()
        db = _FakeDb()
        resp = self._post_trigger(user, db, {"sources": ["gl"]})
        assert resp.status_code == 503
        assert "disabled" in resp.json()["detail"].lower()

    def test_trigger_validates_sources(self, env_configured):
        user = _make_user()
        db = _FakeDb()
        resp = self._post_trigger(user, db, {"sources": ["gl", "not_a_thing"]})
        assert resp.status_code == 422

    def test_trigger_rejects_empty_sources(self, env_configured):
        user = _make_user()
        db = _FakeDb()
        resp = self._post_trigger(user, db, {"sources": []})
        assert resp.status_code == 422

    def test_trigger_validates_mode(self, env_configured):
        user = _make_user()
        db = _FakeDb()
        resp = self._post_trigger(user, db, {"sources": ["gl"], "mode": "magic"})
        assert resp.status_code == 422

    def test_trigger_validates_basis(self, env_configured):
        user = _make_user()
        db = _FakeDb()
        resp = self._post_trigger(user, db, {"sources": ["gl"], "basis": "fifo"})
        assert resp.status_code == 422

    def test_trigger_validates_date_range_format(self, env_configured):
        user = _make_user()
        db = _FakeDb()
        resp = self._post_trigger(
            user, db,
            {"sources": ["gl"], "date_from": "May 1, 2030"},
        )
        assert resp.status_code == 422

    def test_trigger_creates_record_and_returns_202(self, env_configured):
        user = _make_user()
        db = _FakeDb()
        resp = self._post_trigger(
            user, db,
            {"sources": ["gl", "invoices"], "mode": "incremental"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["sync_request_id"].startswith("qbosync_")
        assert body["status"] == "acknowledged"
        assert body["status_url"].endswith(body["sync_request_id"])
        # One record persisted, status flipped through queued ->
        # acknowledged.
        assert len(db.qbo_sync_requests.docs) == 1
        rec = db.qbo_sync_requests.docs[0]
        assert rec["status"] == "acknowledged"
        assert rec["sources"] == ["gl", "invoices"]
        assert rec["requested_by_user_id"] == "u_001"

    def test_trigger_signs_outgoing_payload(self, env_configured):
        # Capture the X-AEGISAXIOM-Signature and verify it equals the
        # HMAC over the body. Confirms AEGISAXIOM isn't accidentally
        # sending an unsigned payload.
        captured: dict = {}

        async def _fake_post(self, url, content=None, headers=None):
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = headers
            return MagicMock(
                status_code=200,
                json=lambda: {"accepted": True, "n8n_execution_id": "exec"},
            )

        async def _fake_aclose(self):
            return None

        user = _make_user()
        db = _FakeDb()
        app, _ = _build_app(user, db)
        patches = _patch_db(db) + [
            patch.object(httpx.AsyncClient, "post", _fake_post),
            patch.object(httpx.AsyncClient, "aclose", _fake_aclose),
        ]
        started, opened = _enter_all(patches)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/qbo/sync/trigger",
                json={"sources": ["gl"]},
            )
        finally:
            _exit_all(opened)
        assert resp.status_code == 202
        assert captured["headers"]["X-AEGISAXIOM-Signature"].startswith("sha256=")
        digest = captured["headers"]["X-AEGISAXIOM-Signature"].split("=", 1)[1]
        expected = hmac.new(
            _FAKE_OUTBOUND_SECRET.encode(),
            captured["content"],
            hashlib.sha256,
        ).hexdigest()
        assert hmac.compare_digest(digest, expected)
        # And we POSTed to the configured URL, not a hardcoded one.
        assert captured["url"] == _FAKE_N8N_URL

    def test_trigger_sends_absolute_callback_url(self, env_configured):
        captured: dict = {}

        async def _fake_post(self, url, content=None, headers=None):
            captured["body"] = json.loads(content.decode())
            return MagicMock(
                status_code=200,
                json=lambda: {"accepted": True},
            )

        async def _fake_aclose(self):
            return None

        user = _make_user()
        db = _FakeDb()
        app, _ = _build_app(user, db)
        patches = _patch_db(db) + [
            patch.object(httpx.AsyncClient, "post", _fake_post),
            patch.object(httpx.AsyncClient, "aclose", _fake_aclose),
        ]
        started, opened = _enter_all(patches)
        try:
            client = TestClient(app)
            client.post("/api/qbo/sync/trigger", json={"sources": ["gl"]})
        finally:
            _exit_all(opened)
        assert captured["body"]["callback_url"] == (
            f"{_FAKE_AEGISAXIOM_BASE}/api/qbo/sync/callback"
        )

    def test_trigger_409_when_running_sync_already_in_progress(self, env_configured):
        # MEDIUM-6: a UI double-click or burst of authorized POSTs
        # must not queue parallel n8n workflows. The trigger checks
        # find_running and returns 409 if one is already in flight.
        from services.qbo_sync_service import build_sync_request_record
        user = _make_user()
        db = _FakeDb()
        # Pre-seed a running record.
        running = build_sync_request_record(
            sync_request_id="qbosync_already_running",
            requested_by_user_id="u_001",
            requested_source="manual",
            sources=["gl"],
            mode="incremental",
            basis="accrual",
            date_from=None,
            date_to=None,
        )
        running["status"] = "running"
        asyncio.run(db.qbo_sync_requests.insert_one(running))
        resp = self._post_trigger(user, db, {"sources": ["gl"]})
        assert resp.status_code == 409
        assert "already in progress" in resp.json()["detail"]

    def test_trigger_n8n_failure_marks_record_failed(self, env_configured):
        user = _make_user()
        db = _FakeDb()
        resp = self._post_trigger(
            user, db,
            {"sources": ["gl"]},
            n8n_response=MagicMock(
                status_code=500,
                json=lambda: {"error": "boom"},
            ),
        )
        assert resp.status_code == 502
        rec = db.qbo_sync_requests.docs[0]
        assert rec["status"] == "failed"
        assert rec["errors"] and rec["errors"][0]["reason"] == "n8n_unreachable"

    def test_trigger_config_error_scrubs_env_var_name_from_db_record(
        self, env_configured
    ):
        """PR cleanup B3. When post_to_n8n raises QboSyncConfigError
        the route persists a fixed scrubbed string in the DB record
        errors array. The original exception message contains the
        missing env var name. The DB record must not expose it,
        because the status endpoint surfaces errors to any caller
        with finance.meta.view permission and that audience is
        broader than the deploy operator who needs the env var name.
        The full message stays in the server log via logger.error
        and in the audit log details only."""
        async def _raise_config(*args, **kwargs):
            raise svc.QboSyncConfigError(
                "N8N_QBO_SYNC_SECRET is not configured"
            )

        user = _make_user()
        db = _FakeDb()
        app, _ = _build_app(user, db)
        patches = _patch_db(db) + [
            patch("routes.qbo_sync.post_to_n8n", _raise_config),
        ]
        started, opened = _enter_all(patches)
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/qbo/sync/trigger", json={"sources": ["gl"]},
            )
        finally:
            _exit_all(opened)
        assert resp.status_code == 503, resp.text
        assert len(db.qbo_sync_requests.docs) == 1
        rec = db.qbo_sync_requests.docs[0]
        assert rec["status"] == "failed"
        assert rec["errors"], "expected an errors entry"
        entry = rec["errors"][0]
        assert entry["reason"] == "configuration"
        assert entry["detail"] == "server configuration error"
        for env_var_name in (
            "N8N_QBO_SYNC_SECRET",
            "N8N_QBO_SYNC_WEBHOOK_URL",
            "AEGISAXIOM_QBO_SYNC_CALLBACK_SECRET",
        ):
            assert env_var_name not in entry["detail"], (
                "env var name leaked into DB record errors entry"
            )


# ---------------------------------------------------------------------------
# Phase 3 — callback endpoint
# ---------------------------------------------------------------------------


class TestCallbackEndpoint:
    def _seed_record(self, db, **overrides):
        from services.qbo_sync_service import build_sync_request_record

        record = build_sync_request_record(
            sync_request_id=overrides.get("sync_request_id", "qbosync_seed"),
            requested_by_user_id="u_001",
            requested_source="manual",
            sources=overrides.get("sources", ["gl"]),
            mode="incremental",
            basis="accrual",
            date_from=None,
            date_to=None,
        )
        record["status"] = overrides.get("status", "acknowledged")
        record["n8n_execution_id"] = overrides.get("n8n_execution_id")
        # Sneak in to the in-memory store.
        asyncio.run(db.qbo_sync_requests.insert_one(record))
        return record

    def _signed_post(self, app, body_dict: dict, *, signature: Optional[str] = None,
                     header_id: Optional[str] = "qbosync_seed"):
        body_bytes = json.dumps(
            body_dict, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if signature is None:
            digest = hmac.new(
                _FAKE_INBOUND_SECRET.encode(), body_bytes, hashlib.sha256
            ).hexdigest()
            signature = f"sha256={digest}"
        headers = {"X-N8N-Signature": signature, "Content-Type": "application/json"}
        if header_id is not None:
            headers["X-AEGISAXIOM-Sync-Request-Id"] = header_id
        client = TestClient(app)
        return client.post("/api/qbo/sync/callback", content=body_bytes, headers=headers)

    def test_callback_rejects_missing_signature(self, env_configured):
        db = _FakeDb()
        self._seed_record(db)
        app, _ = _build_app(None, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            client = TestClient(app)
            # Header is now required per PR cleanup A5 and runs
            # before signature verification. Supply the header so
            # the missing signature path is what surfaces.
            resp = client.post(
                "/api/qbo/sync/callback",
                json={"sync_request_id": "qbosync_seed", "status": "running"},
                headers={"X-AEGISAXIOM-Sync-Request-Id": "qbosync_seed"},
            )
        finally:
            _exit_all(opened)
        assert resp.status_code == 401

    def test_callback_rejects_invalid_signature(self, env_configured):
        db = _FakeDb()
        self._seed_record(db)
        app, _ = _build_app(None, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            resp = self._signed_post(
                app,
                {"sync_request_id": "qbosync_seed", "status": "running"},
                signature="sha256=00",
            )
        finally:
            _exit_all(opened)
        assert resp.status_code == 401

    def test_callback_rejects_unknown_request_id(self, env_configured):
        db = _FakeDb()
        # NOTE: not seeding any record.
        app, _ = _build_app(None, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            resp = self._signed_post(
                app,
                {"sync_request_id": "qbosync_missing", "status": "running"},
                header_id="qbosync_missing",
            )
        finally:
            _exit_all(opened)
        assert resp.status_code == 404

    def test_callback_running_updates_status(self, env_configured):
        db = _FakeDb()
        self._seed_record(db, status="acknowledged")
        app, _ = _build_app(None, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            resp = self._signed_post(
                app,
                {
                    "sync_request_id": "qbosync_seed",
                    "status": "running",
                    "started_at": "2030-05-01T00:00:00+00:00",
                },
            )
        finally:
            _exit_all(opened)
        assert resp.status_code == 200
        rec = db.qbo_sync_requests.docs[0]
        assert rec["status"] == "running"
        assert rec["callback_received"] is True
        assert rec["started_at"] is not None

    def test_callback_complete_records_counts(self, env_configured):
        db = _FakeDb()
        self._seed_record(db, status="running")
        app, _ = _build_app(None, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            resp = self._signed_post(
                app,
                {
                    "sync_request_id": "qbosync_seed",
                    "status": "complete",
                    "finished_at": "2030-05-01T01:00:00+00:00",
                    "counts": {"gl_rows": 1234, "invoices": 56},
                    "errors": [],
                },
            )
        finally:
            _exit_all(opened)
        assert resp.status_code == 200
        rec = db.qbo_sync_requests.docs[0]
        assert rec["status"] == "complete"
        assert rec["counts"] == {"gl_rows": 1234, "invoices": 56}
        assert rec["finished_at"] is not None

    def test_callback_failed_records_errors(self, env_configured):
        db = _FakeDb()
        self._seed_record(db, status="running")
        app, _ = _build_app(None, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            resp = self._signed_post(
                app,
                {
                    "sync_request_id": "qbosync_seed",
                    "status": "failed",
                    "errors": [{"step": "Fetch GL", "detail": "auth expired"}],
                },
            )
        finally:
            _exit_all(opened)
        assert resp.status_code == 200
        rec = db.qbo_sync_requests.docs[0]
        assert rec["status"] == "failed"
        assert rec["errors"][0]["step"] == "Fetch GL"

    def test_callback_idempotent_on_duplicate_complete(self, env_configured):
        db = _FakeDb()
        self._seed_record(db, status="running")
        app, _ = _build_app(None, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            payload = {
                "sync_request_id": "qbosync_seed",
                "status": "complete",
                "counts": {"gl_rows": 100},
            }
            r1 = self._signed_post(app, payload)
            r2 = self._signed_post(app, payload)
        finally:
            _exit_all(opened)
        assert r1.status_code == 200 and r2.status_code == 200
        rec = db.qbo_sync_requests.docs[0]
        # Counts replaced, not accumulated.
        assert rec["counts"] == {"gl_rows": 100}
        assert rec["status"] == "complete"

    def test_callback_running_with_finished_at_does_not_stamp_finish(self, env_configured):
        # HIGH-3: a running callback that happens to carry a
        # finished_at field must NOT stamp the record's finished_at.
        # Doing so would corrupt get_finance_data_freshness while the
        # sync is still live.
        db = _FakeDb()
        self._seed_record(db, status="acknowledged")
        app, _ = _build_app(None, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            resp = self._signed_post(
                app,
                {
                    "sync_request_id": "qbosync_seed",
                    "status": "running",
                    "started_at": "2030-05-01T00:00:00+00:00",
                    "finished_at": "2030-05-01T01:00:00+00:00",
                },
            )
        finally:
            _exit_all(opened)
        assert resp.status_code == 200
        rec = db.qbo_sync_requests.docs[0]
        assert rec["status"] == "running"
        # finished_at must remain None — running is not terminal.
        assert rec["finished_at"] is None
        # started_at IS allowed.
        assert rec["started_at"] is not None

    def test_callback_rejects_oversized_body(self, env_configured):
        # MEDIUM-7: HMAC verification must not be asked to hash an
        # unbounded body. A signed-but-malicious replay with a giant
        # payload should be refused with 413 BEFORE hashing.
        db = _FakeDb()
        self._seed_record(db)
        app, _ = _build_app(None, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            big = b"x" * (300 * 1024)  # 300 KiB > 256 KiB cap
            client = TestClient(app)
            resp = client.post(
                "/api/qbo/sync/callback",
                content=big,
                headers={
                    "X-N8N-Signature": "sha256=00",
                    "X-AEGISAXIOM-Sync-Request-Id": "qbosync_seed",
                    "Content-Type": "application/json",
                },
            )
        finally:
            _exit_all(opened)
        assert resp.status_code == 413
        assert "size" in resp.json()["detail"].lower()

    def test_callback_rejects_too_many_errors(self, env_configured):
        # MEDIUM-7: errors list capped at 200 entries.
        db = _FakeDb()
        self._seed_record(db, status="running")
        app, _ = _build_app(None, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            body = {
                "sync_request_id": "qbosync_seed",
                "status": "failed",
                "errors": [{"i": i} for i in range(201)],
            }
            resp = self._signed_post(app, body)
        finally:
            _exit_all(opened)
        assert resp.status_code == 400

    def test_callback_rejects_too_many_counts_keys(self, env_configured):
        db = _FakeDb()
        self._seed_record(db, status="running")
        app, _ = _build_app(None, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            body = {
                "sync_request_id": "qbosync_seed",
                "status": "complete",
                "counts": {f"k{i}": i for i in range(60)},
            }
            resp = self._signed_post(app, body)
        finally:
            _exit_all(opened)
        assert resp.status_code == 400

    def test_callback_complete_then_running_does_not_regress(self, env_configured):
        db = _FakeDb()
        self._seed_record(db, status="running")
        app, _ = _build_app(None, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            self._signed_post(
                app,
                {
                    "sync_request_id": "qbosync_seed",
                    "status": "complete",
                    "counts": {"gl_rows": 9},
                },
            )
            late = self._signed_post(
                app,
                {
                    "sync_request_id": "qbosync_seed",
                    "status": "running",
                },
            )
        finally:
            _exit_all(opened)
        # The late running callback is recorded but ignored — status
        # stays complete.
        assert late.status_code == 200
        rec = db.qbo_sync_requests.docs[0]
        assert rec["status"] == "complete"
        assert late.json()["accepted"] is False


# ---------------------------------------------------------------------------
# Phase 4 — status / latest endpoints
# ---------------------------------------------------------------------------


class TestStatusEndpoints:
    def _seed_record(self, db, **kw):
        from services.qbo_sync_service import build_sync_request_record

        rec = build_sync_request_record(
            sync_request_id=kw.get("sync_request_id", "qbosync_status"),
            requested_by_user_id="u_001",
            requested_source="manual",
            sources=kw.get("sources", ["gl"]),
            mode="incremental",
            basis="accrual",
            date_from=None,
            date_to=None,
        )
        for k, v in kw.items():
            if k in rec:
                rec[k] = v
        asyncio.run(db.qbo_sync_requests.insert_one(rec))
        return rec

    def test_status_returns_record(self, env_configured):
        user = _make_user()
        db = _FakeDb()
        self._seed_record(db, status="running", sync_request_id="qbosync_aaa")
        app, _ = _build_app(user, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            resp = TestClient(app).get("/api/qbo/sync/status/qbosync_aaa")
        finally:
            _exit_all(opened)
        assert resp.status_code == 200
        body = resp.json()
        assert body["sync_request_id"] == "qbosync_aaa"
        assert body["status"] == "running"

    def test_latest_returns_freshness_when_complete(self, env_configured):
        user = _make_user()
        db = _FakeDb()
        finished_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        self._seed_record(
            db,
            status="complete",
            finished_at=finished_at,
            sync_request_id="qbosync_done",
        )
        app, _ = _build_app(user, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            resp = TestClient(app).get("/api/qbo/sync/latest")
        finally:
            _exit_all(opened)
        assert resp.status_code == 200
        body = resp.json()
        assert body["last_status"] == "complete"
        assert body["is_stale"] is False
        assert body["sync_lag_seconds"] is not None
        assert body["last_completed_sync_request_id"] == "qbosync_done"

    def test_latest_marks_stale_when_threshold_exceeded(self, env_configured, monkeypatch):
        # Threshold 60s — make the last successful sync 2h old.
        monkeypatch.setenv("QBO_SYNC_STALE_AFTER_SECONDS", "60")
        user = _make_user()
        db = _FakeDb()
        long_ago = datetime.now(timezone.utc) - timedelta(hours=2)
        self._seed_record(db, status="complete", finished_at=long_ago)
        app, _ = _build_app(user, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            resp = TestClient(app).get("/api/qbo/sync/latest")
        finally:
            _exit_all(opened)
        assert resp.json()["is_stale"] is True


# ---------------------------------------------------------------------------
# Phase 5 — Nova freshness helper
# ---------------------------------------------------------------------------


class TestNovaFreshnessHelper:
    def test_returns_warning_when_stale(self, env_configured, monkeypatch):
        monkeypatch.setenv("QBO_SYNC_STALE_AFTER_SECONDS", "60")
        db = _FakeDb()
        long_ago = datetime.now(timezone.utc) - timedelta(hours=2)
        from services.qbo_sync_service import build_sync_request_record
        rec = build_sync_request_record(
            sync_request_id="qbosync_stale",
            requested_by_user_id="u_001",
            requested_source="manual",
            sources=["gl"],
            mode="incremental",
            basis="accrual",
            date_from=None,
            date_to=None,
        )
        rec["status"] = "complete"
        rec["finished_at"] = long_ago
        asyncio.run(db.qbo_sync_requests.insert_one(rec))

        out = asyncio.run(svc.get_finance_data_freshness(db))
        assert out["is_stale"] is True
        assert "stale" in out["warning"].lower()

    def test_returns_no_warning_when_fresh(self, env_configured):
        db = _FakeDb()
        from services.qbo_sync_service import build_sync_request_record
        rec = build_sync_request_record(
            sync_request_id="qbosync_fresh",
            requested_by_user_id="u_001",
            requested_source="manual",
            sources=["gl"],
            mode="incremental",
            basis="accrual",
            date_from=None,
            date_to=None,
        )
        rec["status"] = "complete"
        rec["finished_at"] = datetime.now(timezone.utc) - timedelta(seconds=30)
        asyncio.run(db.qbo_sync_requests.insert_one(rec))

        out = asyncio.run(svc.get_finance_data_freshness(db))
        assert out["is_stale"] is False
        assert out["warning"] is None

    def test_no_record_means_stale(self, env_configured):
        db = _FakeDb()
        out = asyncio.run(svc.get_finance_data_freshness(db))
        assert out["is_stale"] is True
        assert out["last_successful_sync_at"] is None

    def test_helper_does_not_call_qbo_or_n8n(self, env_configured, monkeypatch):
        # Ban httpx — if Nova accidentally reaches out, this fails.
        called = []

        async def _bomb(*a, **kw):
            called.append(("post", a, kw))
            raise AssertionError("freshness helper must not POST anywhere")

        monkeypatch.setattr(httpx.AsyncClient, "post", _bomb)
        monkeypatch.setattr(httpx.AsyncClient, "get", _bomb)
        db = _FakeDb()
        out = asyncio.run(svc.get_finance_data_freshness(db))
        assert called == []
        assert "is_stale" in out


# ---------------------------------------------------------------------------
# Phase 6 — audit events
# ---------------------------------------------------------------------------


class TestAuditEvents:
    def test_trigger_emits_triggered_and_acknowledged(self, env_configured):
        async def _fake_post(self, url, content=None, headers=None):
            return MagicMock(status_code=200, json=lambda: {"accepted": True})

        async def _fake_aclose(self):
            return None

        user = _make_user()
        db = _FakeDb()
        app, _ = _build_app(user, db)
        patches = _patch_db(db) + [
            patch.object(httpx.AsyncClient, "post", _fake_post),
            patch.object(httpx.AsyncClient, "aclose", _fake_aclose),
        ]
        started, opened = _enter_all(patches)
        try:
            TestClient(app).post(
                "/api/qbo/sync/trigger",
                json={"sources": ["gl"]},
            )
        finally:
            _exit_all(opened)
        actions = [e["action"] for e in db.audit_logs.entries]
        assert "qbo.sync.triggered" in actions
        assert "qbo.sync.acknowledged" in actions

    def test_callback_complete_emits_completed_event(self, env_configured):
        from services.qbo_sync_service import build_sync_request_record

        db = _FakeDb()
        rec = build_sync_request_record(
            sync_request_id="qbosync_audit",
            requested_by_user_id="u_001",
            requested_source="manual",
            sources=["gl"],
            mode="incremental",
            basis="accrual",
            date_from=None,
            date_to=None,
        )
        rec["status"] = "running"
        asyncio.run(db.qbo_sync_requests.insert_one(rec))

        body = {
            "sync_request_id": "qbosync_audit",
            "status": "complete",
            "counts": {"gl_rows": 7},
        }
        body_bytes = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hmac.new(
            _FAKE_INBOUND_SECRET.encode(), body_bytes, hashlib.sha256
        ).hexdigest()
        headers = {
            "X-N8N-Signature": f"sha256={digest}",
            "X-AEGISAXIOM-Sync-Request-Id": "qbosync_audit",
            "Content-Type": "application/json",
        }
        app, _ = _build_app(None, db)
        patches = _patch_db(db)
        started, opened = _enter_all(patches)
        try:
            TestClient(app).post(
                "/api/qbo/sync/callback", content=body_bytes, headers=headers
            )
        finally:
            _exit_all(opened)
        actions = [e["action"] for e in db.audit_logs.entries]
        assert "qbo.sync.completed" in actions


# ---------------------------------------------------------------------------
# Phase 7 — config / hardcode guard
# ---------------------------------------------------------------------------


class TestConfigGuards:
    def test_no_production_urls_in_service(self):
        # The service file MUST NOT mention any real domain. Reading
        # the source rather than the runtime env so a future refactor
        # that hardcodes a string still trips this.
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "services", "qbo_sync_service.py",
        )
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        # Common production-leak markers — adjust if a new tenant
        # domain shows up.
        # Strip docstrings/comments so a doc-only mention of an env
        # variable doesn't trip the guard. The check targets actual
        # string literals reaching runtime, not prose.
        non_doc_lines = [
            line for line in text.splitlines()
            if not line.lstrip().startswith("#")
            and '"""' not in line
            and not line.strip().startswith("*")
        ]
        # Drop the contiguous lines INSIDE a triple-quoted block.
        in_block = False
        cleaned: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                in_block = not in_block
                continue
            if in_block:
                continue
            if stripped.startswith("#"):
                continue
            cleaned.append(line)
        runtime_text = "\n".join(cleaned)
        for needle in (
            "simplimeta.com", "simplimeta.io",
            "intuit.com", "quickbooks.com",
            "n8n.cloud",
            "mongodb.net",
        ):
            assert needle not in runtime_text, (
                f"hardcoded production string {needle!r} found in runtime code"
            )
        # No stray realm-id literal — appears only allowed inside
        # docstrings explaining where it lives.
        assert "realm_id" not in runtime_text, (
            "service should not reference QBO realm_id outside comments"
        )

    def test_no_test_url_hardcoded_in_routes(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "routes", "qbo_sync.py",
        )
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        # n8n's test URL pattern — never bake into AEGISAXIOM code.
        assert "/webhook-test/" not in text

    def test_contract_doc_exists_and_mentions_production_url(self):
        # docs/qbo/N8N_QBO_SYNC_CONTRACT.md lives at the repo root,
        # one level above backend/.
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        repo_root = os.path.dirname(backend_dir)
        doc_path = os.path.join(
            repo_root, "docs", "qbo", "N8N_QBO_SYNC_CONTRACT.md"
        )
        assert os.path.isfile(doc_path), f"missing {doc_path}"
        with open(doc_path, "r", encoding="utf-8") as f:
            text = f.read()
        # The doc must reference the Production URL distinction loudly
        # and warn against the Test URL.
        assert "Production" in text
        assert "test URL" in text or "Test URL" in text
        assert "N8N_QBO_SYNC_WEBHOOK_URL" in text
        assert "AEGISAXIOM_PUBLIC_API_BASE_URL" in text
        assert "OAuth Redirect URL" in text


# ---------------------------------------------------------------------------
# Phase 8 — reconciliation pure function
# ---------------------------------------------------------------------------


class TestReconcileCallbackStatus:
    def test_idempotent_same_status(self):
        out, accepted = svc.reconcile_callback_status(
            current_status="running", incoming_status="running"
        )
        assert out == "running" and accepted is True

    def test_running_progresses_to_complete(self):
        out, accepted = svc.reconcile_callback_status(
            current_status="running", incoming_status="complete"
        )
        assert out == "complete" and accepted is True

    def test_complete_does_not_regress_to_running(self):
        out, accepted = svc.reconcile_callback_status(
            current_status="complete", incoming_status="running"
        )
        assert out == "complete" and accepted is False

    def test_failed_does_not_progress_to_complete(self):
        out, accepted = svc.reconcile_callback_status(
            current_status="failed", incoming_status="complete"
        )
        assert out == "failed" and accepted is False

    def test_unknown_incoming_is_ignored(self):
        out, accepted = svc.reconcile_callback_status(
            current_status="acknowledged", incoming_status="garbage"
        )
        assert out == "acknowledged" and accepted is False


# ---------------------------------------------------------------------------
# PR #158 review backlog Fix 11 — QboSyncConfigError propagates from
# post_to_n8n via the now-live except branch
# ---------------------------------------------------------------------------


class TestPostToN8nQboSyncConfigError:
    """The earlier shape called sign_outbound_payload() and
    n8n_webhook_url() ABOVE the try/except, so the documented
    ``except QboSyncConfigError: raise`` branch was unreachable from
    those calls. Fix 11 moves them inside the try; both branches now
    cover the helper failure paths.
    """

    def test_missing_outbound_secret_raises_config_error(self, monkeypatch):
        # No envs set — sign_outbound_payload calls _outbound_secret
        # which raises QboSyncConfigError. Now that the call is
        # inside the try, the except QboSyncConfigError branch
        # catches and re-raises it; the caller sees the propagated
        # exception type.
        for var in (
            "N8N_QBO_SYNC_SECRET",
            "N8N_QBO_SYNC_WEBHOOK_URL",
            "AEGISAXIOM_PUBLIC_API_BASE_URL",
        ):
            monkeypatch.delenv(var, raising=False)

        with pytest.raises(svc.QboSyncConfigError):
            asyncio.run(
                svc.post_to_n8n(
                    payload={"sync_request_id": "qbosync_x"},
                    sync_request_id="qbosync_x",
                )
            )

    def test_missing_webhook_url_raises_config_error(self, monkeypatch):
        # Outbound secret present so signing succeeds; n8n_webhook_url
        # is then the source of the QboSyncConfigError. Same fix —
        # the call is inside the try and the except clause re-raises.
        monkeypatch.setenv("N8N_QBO_SYNC_SECRET", "shh")
        monkeypatch.delenv("N8N_QBO_SYNC_WEBHOOK_URL", raising=False)

        with pytest.raises(svc.QboSyncConfigError):
            asyncio.run(
                svc.post_to_n8n(
                    payload={"sync_request_id": "qbosync_y"},
                    sync_request_id="qbosync_y",
                )
            )


# ===========================================================================
# PR #164 review cleanup — migrated from test_pr164_review_cleanup.py
# ===========================================================================
# Atomic sync-slot guard + strict outbound signing serialisation +
# follow-up: signing-error route handling and CallbackBody.counts
# value validation.


def _fake_db_with_duplicate_key_for_acquire():
    """Mock db where insert_one raises DuplicateKeyError, simulating
    the partial unique index rejecting a second concurrent acquire."""
    from pymongo.errors import DuplicateKeyError

    db = MagicMock()
    coll = MagicMock()
    coll.insert_one = AsyncMock(side_effect=DuplicateKeyError("synthetic"))
    coll.find_one = AsyncMock(return_value={
        "sync_request_id": "synth_existing",
        "status": "running",
    })
    db.__getitem__.return_value = coll
    return db, coll


def _fake_db_for_successful_acquire():
    db = MagicMock()
    coll = MagicMock()
    coll.insert_one = AsyncMock(return_value=None)
    coll.find_one = AsyncMock(return_value=None)
    db.__getitem__.return_value = coll
    return db, coll


def test_acquire_sync_slot_returns_existing_on_duplicate_key():
    """The second concurrent acquire collides on the partial unique
    index; the helper returns the existing running record."""
    from services.qbo_sync_service import acquire_sync_slot

    db, _ = _fake_db_with_duplicate_key_for_acquire()
    record = {"sync_request_id": "synth_new", "status": "queued"}
    acquired, existing = asyncio.run(acquire_sync_slot(db, record))
    assert acquired is None
    assert existing is not None
    assert existing["sync_request_id"] == "synth_existing"


def test_acquire_sync_slot_inserts_record_with_active_lock_field():
    """Successful path inserts with ``is_active_lock="global"`` so
    the partial unique index applies."""
    from services.qbo_sync_service import acquire_sync_slot

    db, coll = _fake_db_for_successful_acquire()
    record = {"sync_request_id": "synth_new", "status": "queued"}
    acquired, existing = asyncio.run(acquire_sync_slot(db, record))
    assert existing is None
    assert acquired is not None
    assert acquired["is_active_lock"] == "global"
    coll.insert_one.assert_awaited_once()
    inserted = coll.insert_one.await_args.args[0]
    assert inserted["is_active_lock"] == "global"


def test_release_sync_slot_unsets_lock_field():
    """``release_sync_slot`` $unsets the lock field; idempotent on
    records that never held the lock."""
    from services.qbo_sync_service import release_sync_slot

    db = MagicMock()
    coll = MagicMock()
    coll.update_one = AsyncMock(return_value=None)
    db.__getitem__.return_value = coll

    asyncio.run(release_sync_slot(db, "synth_request_id"))
    coll.update_one.assert_awaited_once()
    args, kwargs = coll.update_one.await_args
    filt = args[0] if args else kwargs.get("filter")
    update = args[1] if len(args) > 1 else kwargs.get("update")
    assert filt == {
        "sync_request_id": "synth_request_id",
        "is_active_lock": "global",
    }
    assert update == {"$unset": {"is_active_lock": ""}}


def test_qbo_sync_index_setup_includes_partial_unique_lock_index():
    """``server.py``'s ensure_indexes block must create the partial
    unique index that backs ``acquire_sync_slot``.

    Reads the server.py source from disk instead of importing the
    module so the assertion does not depend on the optional runtime
    modules pulled in by routes unrelated to qbo sync."""
    import pathlib

    server_path = pathlib.Path(__file__).resolve().parent.parent / "server.py"
    source = server_path.read_text(encoding="utf-8")
    assert "qbo_sync_active_lock_partial_unique" in source
    assert 'partialFilterExpression={"is_active_lock": "global"}' in source


# ── Strict outbound signing serialisation ────────────────────────


def test_serialize_for_signing_accepts_clean_payload():
    """Clean JSON-native payload round-trips deterministically."""
    from services.qbo_sync_service import _serialize_for_signing

    payload = {
        "sync_request_id": "synth_req_1",
        "requested_by_user_id": "u_test",
        "requested_source": "ui",
        "sources": ["gl"],
        "mode": "incremental",
        "basis": "accrual",
        "date_from": None,
        "date_to": None,
        "callback_url": "https://example.invalid/cb",
        "requested_at": "2099-01-01T00:00:00+00:00",
    }
    body = _serialize_for_signing(payload)
    body_again = _serialize_for_signing(payload)
    assert body == body_again
    expected = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert body == expected


def test_serialize_for_signing_rejects_datetime_value():
    """A datetime must raise TypeError. The strict default callable
    refuses non JSON native values so the HMAC canonical form
    cannot diverge between sender and receiver via silent string
    coercion. The thrown message must name the type."""
    from services.qbo_sync_service import _serialize_for_signing

    payload = {
        "sync_request_id": "synth",
        "requested_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
    }
    with pytest.raises(TypeError) as exc_info:
        _serialize_for_signing(payload)
    assert "datetime" in str(exc_info.value)


def test_serialize_for_signing_rejects_arbitrary_object():
    """Arbitrary custom objects also raise TypeError and the message
    names the offending type."""
    from services.qbo_sync_service import _serialize_for_signing

    class _Sentinel:
        pass

    payload = {"sync_request_id": "synth", "obj": _Sentinel()}
    with pytest.raises(TypeError) as exc_info:
        _serialize_for_signing(payload)
    assert "_Sentinel" in str(exc_info.value)


def test_sign_outbound_payload_surfaces_signing_error_for_non_canonical():
    """sign_outbound_payload must NOT swallow the signing TypeError
    so callers can see exactly which field broke the payload."""
    os.environ["QBO_N8N_WEBHOOK_SECRET"] = "synth-secret-not-real"
    try:
        from services.qbo_sync_service import sign_outbound_payload

        payload = {
            "sync_request_id": "synth",
            "bad": datetime(2099, 1, 1, tzinfo=timezone.utc),
        }
        with pytest.raises(TypeError):
            sign_outbound_payload(payload)
    finally:
        os.environ.pop("QBO_N8N_WEBHOOK_SECRET", None)


# ── Follow-up: signing-error route handling ──────────────────────


def test_trigger_route_imports_signing_error_alongside_config_error():
    """routes/qbo_sync.py must import QboSyncSigningError from the
    service module and catch it together with the strict serializer
    TypeError so neither can escape as an uncontrolled FastAPI 500."""
    import inspect

    import routes.qbo_sync as qbo_route

    source = inspect.getsource(qbo_route)
    assert "QboSyncSigningError" in source
    # The route catches both the legacy custom exception and the
    # strict TypeError that the new _strict_default raises.
    assert "QboSyncSigningError, TypeError" in source or "TypeError, QboSyncSigningError" in source


def test_trigger_route_signing_error_branch_releases_slot_and_marks_failed():
    """The signing error branch must release the sync slot, mark
    the sync record failed, and raise a controlled HTTPException
    with a non leaky detail."""
    import inspect

    import routes.qbo_sync as qbo_route

    source = inspect.getsource(qbo_route.trigger_qbo_sync)
    assert "QboSyncSigningError, TypeError" in source or "TypeError, QboSyncSigningError" in source
    assert "release_sync_slot(db, sync_request_id)" in source
    # User-visible detail must NOT echo payload values; verify the
    # exact phrasing the route returns today.
    assert "Failed to serialize outbound QBO sync payload" in source


# ── Follow-up: CallbackBody.counts strict value type ─────────────


def test_callback_body_counts_accepts_int_and_float_values():
    """JSON-numeric counts pass; values are preserved as supplied."""
    from routes.qbo_sync import CallbackBody

    body = CallbackBody(
        sync_request_id="synth_req",
        status="complete",
        counts={"gl": 12, "ar": 7.5},
    )
    assert body.counts == {"gl": 12, "ar": 7.5}


def test_callback_body_counts_rejects_nested_dict_value():
    """The validator must refuse a value that is itself a dict —
    that's the abuse path the review flagged."""
    from pydantic import ValidationError

    from routes.qbo_sync import CallbackBody

    with pytest.raises(ValidationError):
        CallbackBody(
            sync_request_id="synth_req",
            status="complete",
            counts={"gl": {"nested": 1}},
        )


def test_callback_body_counts_rejects_list_value():
    from pydantic import ValidationError

    from routes.qbo_sync import CallbackBody

    with pytest.raises(ValidationError):
        CallbackBody(
            sync_request_id="synth_req",
            status="complete",
            counts={"gl": [1, 2, 3]},
        )


def test_callback_body_counts_rejects_string_value():
    """Strings are excluded because the n8n contract documents
    counts as integers."""
    from pydantic import ValidationError

    from routes.qbo_sync import CallbackBody

    with pytest.raises(ValidationError):
        CallbackBody(
            sync_request_id="synth_req",
            status="complete",
            counts={"gl": "twelve"},
        )


def test_callback_body_counts_rejects_bool_value():
    """``bool`` subclasses int in Python but a True/False in counts
    is almost certainly contract drift; reject it explicitly."""
    from pydantic import ValidationError

    from routes.qbo_sync import CallbackBody

    with pytest.raises(ValidationError):
        CallbackBody(
            sync_request_id="synth_req",
            status="complete",
            counts={"gl": True},
        )


def test_callback_body_counts_rejects_excessive_key_count():
    """Existing top-level key cap still fires after the per-value
    validator was added."""
    from pydantic import ValidationError

    from routes.qbo_sync import CallbackBody, _MAX_COUNTS_KEYS

    too_many = {f"k{i}": i for i in range(_MAX_COUNTS_KEYS + 1)}
    with pytest.raises(ValidationError):
        CallbackBody(
            sync_request_id="synth_req",
            status="complete",
            counts=too_many,
        )


# ===========================================================================
# PR cleanup A5. n8n callback header required
# ===========================================================================


def test_callback_requires_x_aegisaxiom_sync_request_id_header(env_configured):
    """A POST to /api/qbo/sync/callback without X-AEGISAXIOM-Sync-Request-Id
    must return 400 not silently skip the mismatch check. Without
    this guard a valid signature for one request can be replayed
    against a different sync_request_id by omitting the header.

    Uses the local _build_app + _FakeDb fixtures so the test does
    not import the full server module. Importing server pulls in
    optional runtime dependencies that are not part of the unit
    test environment."""
    user = _make_user()
    db = _FakeDb()
    app, _ = _build_app(user, db)
    patches = _patch_db(db)
    started, opened = _enter_all(patches)
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/qbo/sync/callback",
            json={
                "sync_request_id": "synth_req_a5",
                "status": "complete",
            },
            headers={
                # X-AEGISAXIOM-Sync-Request-Id deliberately omitted
                "X-AEGISAXIOM-Signature": "sha256=00",
            },
        )
    finally:
        _exit_all(opened)
    # The header guard fires before signature verification so a 400
    # surfaces even with a bogus signature.
    assert resp.status_code == 400
    assert "X-AEGISAXIOM-Sync-Request-Id" in resp.json().get("detail", "")


# ===========================================================================
# PR cleanup A6. is_active_lock redaction
# ===========================================================================


def test_redact_record_drops_is_active_lock():
    """_redact_record must strip the is_active_lock sentinel so a
    caller with finance.meta.view cannot read the internal
    concurrency token via the status endpoint."""
    from routes.qbo_sync import _redact_record

    record = {
        "sync_request_id": "synth_a6",
        "status": "running",
        "is_active_lock": "global",
        "_internal_blob": {"redact": "me"},
        "counts": {"gl": 7},
    }
    redacted = _redact_record(record)
    assert "is_active_lock" not in redacted
    assert "_internal_blob" not in redacted
    # Non sentinel fields survive.
    assert redacted["sync_request_id"] == "synth_a6"
    assert redacted["status"] == "running"
    assert redacted["counts"] == {"gl": 7}


# ===========================================================================
# PR cleanup A8. Strict default in HMAC payload serializer
# ===========================================================================


def test_strict_default_raises_typeerror_naming_the_type():
    """The new _strict_default callable raises TypeError with a
    message naming the offending value type so an operator can see
    exactly which field broke the payload."""
    from services.qbo_sync_service import _strict_default

    class _SynthCustom:
        pass

    with pytest.raises(TypeError) as exc_info:
        _strict_default(_SynthCustom())
    assert "_SynthCustom" in str(exc_info.value)


def test_serialize_for_signing_with_datetime_raises_typeerror_naming_type():
    """End to end. Signing a payload containing a datetime raises
    TypeError and the message names datetime as the offending
    type. This is the regression lock for A8."""
    from services.qbo_sync_service import _serialize_for_signing

    payload = {
        "sync_request_id": "synth_a8",
        "requested_at": datetime(2099, 7, 4, tzinfo=timezone.utc),
    }
    with pytest.raises(TypeError) as exc_info:
        _serialize_for_signing(payload)
    assert "datetime" in str(exc_info.value)
