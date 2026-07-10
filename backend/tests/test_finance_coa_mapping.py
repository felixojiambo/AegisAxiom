"""Tests for backend/routes/finance_coa_mapping.py.

Uses FastAPI's TestClient with fully mocked DB and auth — no live Mongo required.
Pattern mirrors test_ai_runtime_enforcement.py (the canonical AEGISAXIOM async test style).
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_user(user_id="u_001", email="tester@aegisaxiom.io", permissions=None):
    perms = permissions if permissions is not None else ["finance.coa_mapping.view",
                                                         "finance.coa_mapping.create",
                                                         "finance.coa_mapping.delete"]
    return SimpleNamespace(
        user_id=user_id,
        email=email,
        role="finance_admin",
        permissions=perms,
    )


def _make_coa_doc(coa_mapping_id="fcc_coa_001"):
    return {
        "coa_mapping_id": coa_mapping_id,
        "internal_item": "consulting_revenue",
        "internal_kind": "income",
        "qbo_account_id": "qbo_acct_123",
        "qbo_account_name": "Consulting Revenue",
        "auto_apply": True,
        "created_at": "2026-05-04T12:00:00+00:00",
        "updated_at": "2026-05-04T12:00:00+00:00",
        "created_by": "u_001",
    }


def _build_app(fake_user, fake_db):
    from routes.finance_coa_mapping import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/finance")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return app, fake_db


def _mock_db(docs=None):
    """Return a MagicMock db with finance_coa_mappings collection wired up."""
    docs = docs or []
    coll = MagicMock()
    coll.count_documents = AsyncMock(return_value=len(docs))

    # find(...) returns an async cursor mock
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=docs)
    coll.find = MagicMock(return_value=cursor)

    coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="fake_oid"))
    coll.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))

    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=coll)
    db.__getattr__ = MagicMock(return_value=coll)
    return db, coll


# ---------------------------------------------------------------------------
# List tests
# ---------------------------------------------------------------------------

class TestListCoaMappings:
    def test_list_empty(self):
        user = _make_user()
        db, coll = _mock_db(docs=[])
        app, _ = _build_app(user, db)

        with patch("routes.finance_coa_mapping.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/api/finance/coa-mappings")

        assert resp.status_code == 200
        data = resp.json()
        assert data["rows"] == []
        assert data["total"] == 0

    def test_list_populated(self):
        user = _make_user()
        doc = _make_coa_doc()
        db, coll = _mock_db(docs=[doc])
        app, _ = _build_app(user, db)

        with patch("routes.finance_coa_mapping.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/api/finance/coa-mappings")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["rows"]) == 1
        assert data["rows"][0]["coa_mapping_id"] == "fcc_coa_001"
        assert data["total"] == 1

    def test_list_perm_denied(self):
        user = _make_user(permissions=[])
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        with patch("routes.finance_coa_mapping.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/api/finance/coa-mappings")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Create tests
# ---------------------------------------------------------------------------

class TestCreateCoaMapping:
    def _valid_payload(self):
        return {
            "internal_item": "software_licenses",
            "internal_kind": "expense",
            "qbo_account_id": "qbo_acct_456",
            "qbo_account_name": "Software Licenses",
            "auto_apply": True,
        }

    def test_create_happy_path(self):
        user = _make_user()
        db, coll = _mock_db()
        app, _ = _build_app(user, db)

        async def _noop_envelope(*args, **kwargs):
            from contextlib import asynccontextmanager
            @asynccontextmanager
            async def _ctx():
                ctx = MagicMock()
                ctx.set_after = MagicMock()
                yield ctx
            return _ctx()

        with patch("routes.finance_coa_mapping.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_coa_mapping.crud_envelope") as mock_env:
            from contextlib import asynccontextmanager
            ctx_mock = MagicMock()
            ctx_mock.set_after = MagicMock()

            @asynccontextmanager
            async def _fake_envelope(*args, **kwargs):
                yield ctx_mock

            mock_env.side_effect = _fake_envelope
            client = TestClient(app)
            resp = client.post("/api/finance/coa-mappings", json=self._valid_payload())

        assert resp.status_code == 201
        data = resp.json()
        assert data["internal_kind"] == "expense"
        assert data["coa_mapping_id"].startswith("fcc_coa_")

    def test_create_perm_denied(self):
        user = _make_user(permissions=[])
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        with patch("routes.finance_coa_mapping.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post("/api/finance/coa-mappings", json=self._valid_payload())

        assert resp.status_code == 403

    def test_create_invalid_internal_kind(self):
        user = _make_user()
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        payload = self._valid_payload()
        payload["internal_kind"] = "banana"

        with patch("routes.finance_coa_mapping.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post("/api/finance/coa-mappings", json=payload)

        assert resp.status_code == 422
        assert "internal_kind" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Delete tests
# ---------------------------------------------------------------------------

class TestDeleteCoaMapping:
    def test_delete_happy_path(self):
        user = _make_user()
        doc = _make_coa_doc()
        db, coll = _mock_db(docs=[doc])
        app, _ = _build_app(user, db)

        coll.find_one = AsyncMock(return_value=doc)

        with patch("routes.finance_coa_mapping.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_coa_mapping.crud_envelope") as mock_env:
            from contextlib import asynccontextmanager
            ctx_mock = MagicMock()
            ctx_mock.set_after = MagicMock()

            @asynccontextmanager
            async def _fake_envelope(*args, **kwargs):
                yield ctx_mock

            mock_env.side_effect = _fake_envelope
            client = TestClient(app)
            resp = client.delete("/api/finance/coa-mappings/fcc_coa_001")

        assert resp.status_code == 204

    def test_delete_not_found(self):
        user = _make_user()
        db, coll = _mock_db()
        coll.find_one = AsyncMock(return_value=None)
        app, _ = _build_app(user, db)

        with patch("routes.finance_coa_mapping.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.delete("/api/finance/coa-mappings/nonexistent")

        assert resp.status_code == 404
