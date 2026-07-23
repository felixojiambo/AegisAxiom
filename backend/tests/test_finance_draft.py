"""Tests for backend/routes/finance_draft.py.

Uses FastAPI's TestClient with fully mocked DB and auth — no live Mongo required.
Covers: list (empty + populated), create (happy + perm denied), update (happy + 404),
delete (happy + 404), sync (happy + already-synced 409), kind validation,
sync_status enum validation.
"""
import pytest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_user(user_id="u_001", email="tester@aegisaxiom.io", permissions=None):
    perms = permissions if permissions is not None else [
        "finance.draft.view",
        "finance.draft.create",
        "finance.draft.edit",
        "finance.draft.delete",
        "finance.draft.sync",
    ]
    return SimpleNamespace(
        user_id=user_id,
        email=email,
        role="finance_admin",
        permissions=perms,
    )


def _make_draft_doc(draft_id="fcc_draft_001", kind="ar_invoice", sync_status="Draft"):
    return {
        "draft_id": draft_id,
        "kind": kind,
        "counterparty": "Acme Corp",
        "counterparty_id": None,
        "amount": 1500.00,
        "memo": "Q1 retainer",
        "line_items": [],
        "sync_status": sync_status,
        "qbo_id": None,
        "error": None,
        "source_origin": "manual",
        "source_period_start": None,
        "source_period_end": None,
        "source_contractor_id": None,
        "source_contract_id": None,
        "created_at": "2026-05-04T12:00:00+00:00",
        "updated_at": "2026-05-04T12:00:00+00:00",
        "created_by": "u_001",
    }


def _build_app(fake_user, fake_db):
    from routes.finance_draft import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/finance")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return app, fake_db


def _mock_db(docs=None):
    docs = docs or []
    coll = MagicMock()
    coll.count_documents = AsyncMock(return_value=len(docs))

    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=docs)
    coll.find = MagicMock(return_value=cursor)

    coll.find_one = AsyncMock(return_value=docs[0] if docs else None)
    coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="fake_oid"))
    coll.replace_one = AsyncMock(return_value=MagicMock(modified_count=1))
    coll.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))

    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=coll)
    return db, coll


@asynccontextmanager
async def _fake_envelope(*args, **kwargs):
    ctx = MagicMock()
    ctx.set_after = MagicMock()
    yield ctx


# ---------------------------------------------------------------------------
# List tests
# ---------------------------------------------------------------------------

class TestListDrafts:
    def test_list_empty(self):
        user = _make_user()
        db, _ = _mock_db(docs=[])
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/api/finance/drafts")

        assert resp.status_code == 200
        data = resp.json()
        assert data["rows"] == []
        assert data["total"] == 0

    def test_list_populated(self):
        user = _make_user()
        doc = _make_draft_doc()
        db, _ = _mock_db(docs=[doc])
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/api/finance/drafts")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["rows"]) == 1
        assert data["rows"][0]["draft_id"] == "fcc_draft_001"

    def test_list_perm_denied(self):
        user = _make_user(permissions=[])
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/api/finance/drafts")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Create tests
# ---------------------------------------------------------------------------

class TestCreateDraft:
    def _valid_payload(self):
        return {
            "kind": "ar_invoice",
            "counterparty": "Acme Corp",
            "amount": 1500.00,
        }

    def test_create_happy_path(self):
        user = _make_user()
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.post("/api/finance/drafts", json=self._valid_payload())

        assert resp.status_code == 201
        data = resp.json()
        assert data["draft_id"].startswith("fcc_draft_")
        assert data["kind"] == "ar_invoice"
        assert data["sync_status"] == "Draft"
        assert data["source_origin"] == "manual"

    def test_create_perm_denied(self):
        user = _make_user(permissions=[])
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post("/api/finance/drafts", json=self._valid_payload())

        assert resp.status_code == 403

    def test_create_invalid_kind(self):
        user = _make_user()
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        payload = self._valid_payload()
        payload["kind"] = "wire_transfer"

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post("/api/finance/drafts", json=payload)

        assert resp.status_code == 422
        assert "kind" in resp.json()["detail"]

    def test_create_invalid_sync_status_enum(self):
        user = _make_user()
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        payload = self._valid_payload()
        payload["sync_status"] = "PendingApproval"

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post("/api/finance/drafts", json=payload)

        assert resp.status_code == 422
        assert "sync_status" in resp.json()["detail"]

    def test_create_ap_bill_kind(self):
        user = _make_user()
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        payload = {"kind": "ap_bill", "counterparty": "Vendor Inc", "amount": 999.00}

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.post("/api/finance/drafts", json=payload)

        assert resp.status_code == 201
        assert resp.json()["kind"] == "ap_bill"


# ---------------------------------------------------------------------------
# Update tests
# ---------------------------------------------------------------------------

class TestUpdateDraft:
    def test_update_happy_path(self):
        user = _make_user()
        doc = _make_draft_doc()
        db, coll = _mock_db(docs=[doc])
        coll.find_one = AsyncMock(return_value=doc)
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.patch(
                "/api/finance/drafts/fcc_draft_001",
                json={"memo": "Updated memo", "amount": 2000.00},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["memo"] == "Updated memo"
        assert data["amount"] == 2000.00

    def test_update_not_found(self):
        user = _make_user()
        db, coll = _mock_db()
        coll.find_one = AsyncMock(return_value=None)
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.patch("/api/finance/drafts/nonexistent", json={"memo": "x"})

        assert resp.status_code == 404

    def test_update_kind_ignored_if_sent(self):
        """Confirm kind is not in DraftUpdate — sending it is rejected by extra='forbid'."""
        user = _make_user()
        doc = _make_draft_doc()
        db, coll = _mock_db(docs=[doc])
        coll.find_one = AsyncMock(return_value=doc)
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            # `kind` not in DraftUpdate; extra="forbid" → 422
            resp = client.patch(
                "/api/finance/drafts/fcc_draft_001",
                json={"kind": "ap_bill"},
            )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Delete tests
# ---------------------------------------------------------------------------

class TestDeleteDraft:
    def test_delete_happy_path(self):
        user = _make_user()
        doc = _make_draft_doc()
        db, coll = _mock_db(docs=[doc])
        coll.find_one = AsyncMock(return_value=doc)
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.delete("/api/finance/drafts/fcc_draft_001")

        assert resp.status_code == 204

    def test_delete_not_found(self):
        user = _make_user()
        db, coll = _mock_db()
        coll.find_one = AsyncMock(return_value=None)
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.delete("/api/finance/drafts/nonexistent")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Sync tests
# ---------------------------------------------------------------------------

class TestSyncDraft:
    def test_sync_happy_path(self):
        user = _make_user()
        doc = _make_draft_doc(sync_status="Draft")
        db, coll = _mock_db(docs=[doc])
        coll.find_one = AsyncMock(return_value=doc)
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.post("/api/finance/drafts/fcc_draft_001/sync")

        assert resp.status_code == 200
        data = resp.json()
        assert data["sync_status"] == "Synced"
        assert data["qbo_id"].startswith("SYNC-")
        assert data["error"] is None

    def test_sync_already_synced_409(self):
        user = _make_user()
        doc = _make_draft_doc(sync_status="Synced")
        db, coll = _mock_db(docs=[doc])
        coll.find_one = AsyncMock(return_value=doc)
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post("/api/finance/drafts/fcc_draft_001/sync")

        assert resp.status_code == 409
        assert "already synced" in resp.json()["detail"].lower()

    def test_sync_not_found(self):
        user = _make_user()
        db, coll = _mock_db()
        coll.find_one = AsyncMock(return_value=None)
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post("/api/finance/drafts/nonexistent/sync")

        assert resp.status_code == 404

    def test_sync_perm_denied(self):
        user = _make_user(permissions=["finance.draft.view"])
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post("/api/finance/drafts/fcc_draft_001/sync")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# due_date tests
# ---------------------------------------------------------------------------

class TestDraftDueDate:
    def test_create_with_due_date(self):
        user = _make_user()
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        payload = {
            "kind": "ar_invoice",
            "counterparty": "Acme Corp",
            "amount": 1500.00,
            "due_date": "2026-06-15",
        }

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.post("/api/finance/drafts", json=payload)

        assert resp.status_code == 201
        data = resp.json()
        assert data["due_date"] == "2026-06-15"

    def test_create_without_due_date_defaults_null(self):
        user = _make_user()
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        payload = {"kind": "ar_invoice", "counterparty": "Acme Corp", "amount": 500.00}

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.post("/api/finance/drafts", json=payload)

        assert resp.status_code == 201
        assert resp.json()["due_date"] is None

    def test_update_due_date(self):
        user = _make_user()
        doc = {**_make_draft_doc(), "due_date": None}
        db, coll = _mock_db(docs=[doc])
        coll.find_one = AsyncMock(return_value=doc)
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.patch(
                "/api/finance/drafts/fcc_draft_001",
                json={"due_date": "2026-07-01"},
            )

        assert resp.status_code == 200
        assert resp.json()["due_date"] == "2026-07-01"

    def test_update_due_date_to_null(self):
        user = _make_user()
        doc = {**_make_draft_doc(), "due_date": "2026-06-15"}
        db, coll = _mock_db(docs=[doc])
        coll.find_one = AsyncMock(return_value=doc)
        app, _ = _build_app(user, db)

        with patch("routes.finance_draft.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_draft.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.patch(
                "/api/finance/drafts/fcc_draft_001",
                json={"due_date": None},
            )

        assert resp.status_code == 200
        assert resp.json()["due_date"] is None
