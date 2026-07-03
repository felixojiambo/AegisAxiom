"""Tests for backend/routes/finance_referral_mrr.py.

Uses FastAPI's TestClient with fully mocked DB and auth — no live Mongo required.
Pattern mirrors test_finance_coa_mapping.py (the canonical AEGISAXIOM FCC test style).

Coverage:
  - GET  /api/finance/referral-mrr/header-templates   200 + 403
  - POST /api/finance/referral-mrr/header-templates   201 + 403
  - DELETE /api/finance/referral-mrr/header-templates/{id}  204 + 404 + 403
  - GET  /api/finance/referral-mrr/account-mappings   200 + 403
  - POST /api/finance/referral-mrr/account-mappings   201 (create + upsert) + 403
  - DELETE /api/finance/referral-mrr/account-mappings/{acct}  204 + 404 + 403
  - POST /api/finance/referral-mrr/parse-csv          200 + 400 (missing period) + 403
  - POST /api/finance/referral-mrr/commit-commissions 200 + 403
  - GET  /api/finance/referral-mrr/commissions        200 + 403
"""
import io
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

ROUTE_MODULE = "routes.finance_referral_mrr"
AI_AUTHZ_MODULE = "middleware.ai_authz"


def _make_user(permissions=None):
    perms = permissions if permissions is not None else [
        "finance.referral_mrr.view",
        "finance.referral_mrr.edit",
    ]
    return SimpleNamespace(
        user_id="u_test_001",
        email="tester@aegisaxiom.io",
        role="finance_admin",
        permissions=perms,
    )


def _make_template_doc(template_id="tmpl_abc123"):
    return {
        "template_id": template_id,
        "template_name": "AT&T Template",
        "mappings": {"BAN": "account_number", "Rep Name": "partner_name"},
        "linking_column": "BAN",
        "amount_column": "Revenue",
        "created_by": "u_test_001",
        "created_at": "2026-05-04T12:00:00+00:00",
    }


def _make_account_mapping_doc(account_number="1234567890"):
    return {
        "account_number": account_number,
        "deal_id": "deal_abc",
        "deal_name": "Acme Corp",
        "partner_name": "AT&T",
        "commission_plan": "standard",
        "linking_field_name": "BAN",
        "created_by": "u_test_001",
        "created_at": "2026-05-04T12:00:00+00:00",
    }


def _make_referral_commission_doc(record_id="refmrr_abc"):
    return {
        "record_id": record_id,
        "type": "referral_mrr",
        "account_number": "1234567890",
        "deal_id": "deal_abc",
        "deal_name": "Acme Corp",
        "partner_name": "AT&T",
        "amount": 500.0,
        "period": "2026-01",
        "status": "processed",
        "created_at": "2026-05-04T12:00:00+00:00",
    }


def _mock_collection(docs=None):
    """Return a MagicMock MongoDB collection with common async methods wired up."""
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
    coll.update_one = AsyncMock(return_value=MagicMock(matched_count=1, modified_count=1))
    coll.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    return coll


def _mock_db(collections: dict = None):
    """Build a MagicMock db whose subscript / attr access delegates to per-collection mocks."""
    collections = collections or {}
    db = MagicMock()

    def _getitem(name):
        return collections.get(name, _mock_collection())

    def _getattr(name):
        return collections.get(name, _mock_collection())

    db.__getitem__ = MagicMock(side_effect=_getitem)
    db.__getattr__ = MagicMock(side_effect=_getattr)
    return db


def _build_app(fake_user):
    from routes.finance_referral_mrr import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/finance")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return app


@asynccontextmanager
async def _fake_envelope(*args, **kwargs):
    ctx = MagicMock()
    ctx.set_after = MagicMock()
    yield ctx


# ---------------------------------------------------------------------------
# Header Template tests
# ---------------------------------------------------------------------------

class TestListHeaderTemplates:
    def test_list_empty(self):
        user = _make_user()
        db = _mock_db({"header_templates": _mock_collection(docs=[])})
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).get("/api/finance/referral-mrr/header-templates")

        assert resp.status_code == 200
        assert resp.json() == {"templates": []}

    def test_list_populated(self):
        user = _make_user()
        doc = _make_template_doc()
        db = _mock_db({"header_templates": _mock_collection(docs=[doc])})
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).get("/api/finance/referral-mrr/header-templates")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["templates"]) == 1
        assert data["templates"][0]["template_id"] == "tmpl_abc123"

    def test_list_perm_denied(self):
        user = _make_user(permissions=[])
        db = _mock_db()
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).get("/api/finance/referral-mrr/header-templates")

        assert resp.status_code == 403


class TestCreateHeaderTemplate:
    _payload = {
        "template_name": "AT&T Template",
        "mappings": {"BAN": "account_number"},
        "linking_column": "BAN",
        "amount_column": "Revenue",
    }

    def test_create_happy_path(self):
        user = _make_user()
        db = _mock_db({"header_templates": _mock_collection()})
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None), \
             patch(f"{ROUTE_MODULE}.crud_envelope", side_effect=_fake_envelope):
            resp = TestClient(app).post(
                "/api/finance/referral-mrr/header-templates", json=self._payload
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["template_id"].startswith("tmpl_")
        assert data["message"] == "Template saved"

    def test_create_perm_denied(self):
        user = _make_user(permissions=[])
        db = _mock_db()
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).post(
                "/api/finance/referral-mrr/header-templates", json=self._payload
            )

        assert resp.status_code == 403


class TestDeleteHeaderTemplate:
    def test_delete_happy_path(self):
        user = _make_user()
        doc = _make_template_doc()
        coll = _mock_collection(docs=[doc])
        coll.find_one = AsyncMock(return_value=doc)
        db = _mock_db({"header_templates": coll})
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None), \
             patch(f"{ROUTE_MODULE}.crud_envelope", side_effect=_fake_envelope):
            resp = TestClient(app).delete("/api/finance/referral-mrr/header-templates/tmpl_abc123")

        assert resp.status_code == 204

    def test_delete_not_found(self):
        user = _make_user()
        coll = _mock_collection()
        coll.find_one = AsyncMock(return_value=None)
        db = _mock_db({"header_templates": coll})
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).delete("/api/finance/referral-mrr/header-templates/nonexistent")

        assert resp.status_code == 404

    def test_delete_perm_denied(self):
        user = _make_user(permissions=[])
        db = _mock_db()
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).delete("/api/finance/referral-mrr/header-templates/tmpl_abc123")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Account Mapping tests
# ---------------------------------------------------------------------------

class TestListAccountMappings:
    def test_list_empty(self):
        user = _make_user()
        db = _mock_db({"account_mappings": _mock_collection(docs=[])})
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).get("/api/finance/referral-mrr/account-mappings")

        assert resp.status_code == 200
        assert resp.json() == {"mappings": []}

    def test_list_populated(self):
        user = _make_user()
        doc = _make_account_mapping_doc()
        db = _mock_db({"account_mappings": _mock_collection(docs=[doc])})
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).get("/api/finance/referral-mrr/account-mappings")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["mappings"]) == 1
        assert data["mappings"][0]["account_number"] == "1234567890"

    def test_list_perm_denied(self):
        user = _make_user(permissions=[])
        db = _mock_db()
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).get("/api/finance/referral-mrr/account-mappings")

        assert resp.status_code == 403


class TestSaveAccountMapping:
    _payload = {
        "account_number": "9876543210",
        "deal_id": "deal_xyz",
        "deal_name": "Widget Corp",
        "partner_name": "Verizon",
        "commission_plan": "standard",
        "linking_field_name": "BAN",
    }

    def test_create_new_mapping(self):
        user = _make_user()
        coll = _mock_collection()
        coll.find_one = AsyncMock(return_value=None)  # no existing
        db = _mock_db({"account_mappings": coll})
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None), \
             patch(f"{ROUTE_MODULE}.crud_envelope", side_effect=_fake_envelope):
            resp = TestClient(app).post(
                "/api/finance/referral-mrr/account-mappings", json=self._payload
            )

        assert resp.status_code == 201
        assert resp.json()["message"] == "Account mapping created"

    def test_update_existing_mapping(self):
        user = _make_user()
        existing = _make_account_mapping_doc(account_number="9876543210")
        coll = _mock_collection(docs=[existing])
        coll.find_one = AsyncMock(return_value=existing)
        db = _mock_db({"account_mappings": coll})
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None), \
             patch(f"{ROUTE_MODULE}.crud_envelope", side_effect=_fake_envelope):
            resp = TestClient(app).post(
                "/api/finance/referral-mrr/account-mappings", json=self._payload
            )

        assert resp.status_code == 201
        assert resp.json()["message"] == "Account mapping updated"

    def test_create_perm_denied(self):
        user = _make_user(permissions=[])
        db = _mock_db()
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).post(
                "/api/finance/referral-mrr/account-mappings", json=self._payload
            )

        assert resp.status_code == 403


class TestDeleteAccountMapping:
    def test_delete_happy_path(self):
        user = _make_user()
        doc = _make_account_mapping_doc()
        coll = _mock_collection(docs=[doc])
        coll.find_one = AsyncMock(return_value=doc)
        db = _mock_db({"account_mappings": coll})
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None), \
             patch(f"{ROUTE_MODULE}.crud_envelope", side_effect=_fake_envelope):
            resp = TestClient(app).delete(
                "/api/finance/referral-mrr/account-mappings/1234567890"
            )

        assert resp.status_code == 204

    def test_delete_not_found(self):
        user = _make_user()
        coll = _mock_collection()
        coll.find_one = AsyncMock(return_value=None)
        db = _mock_db({"account_mappings": coll})
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).delete(
                "/api/finance/referral-mrr/account-mappings/nonexistent"
            )

        assert resp.status_code == 404

    def test_delete_perm_denied(self):
        user = _make_user(permissions=[])
        db = _mock_db()
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).delete(
                "/api/finance/referral-mrr/account-mappings/1234567890"
            )

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# CSV parse tests
# ---------------------------------------------------------------------------

class TestParseCSV:
    _csv_content = "BAN,Revenue,Rep Name\n1234567890,500.00,John Smith\n9999999999,250.00,Jane Doe\n"

    def _build_db_for_parse(self, account_docs=None):
        account_docs = account_docs or []
        acct_coll = _mock_collection(docs=account_docs)
        acct_coll.find_one = AsyncMock(return_value=None)

        commissions_coll = _mock_collection(docs=[])
        commissions_coll.find_one = AsyncMock(return_value=None)

        referral_coll = _mock_collection()
        local_coll = _mock_collection()

        return _mock_db(
            {
                "account_mappings": acct_coll,
                "commissions": commissions_coll,
                "referral_commissions": referral_coll,
                "local_commissions": local_coll,
            }
        )

    def test_missing_period_returns_400(self):
        user = _make_user()
        db = self._build_db_for_parse()
        app = _build_app(user)

        csv_bytes = self._csv_content.encode("utf-8")

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).post(
                "/api/finance/referral-mrr/parse-csv",
                data={"linking_column": "BAN", "amount_column": "Revenue"},
                files={"file": ("test.csv", csv_bytes, "text/csv")},
            )

        assert resp.status_code == 400
        assert "period" in resp.json()["detail"].lower()

    def test_parse_with_no_matches(self):
        user = _make_user()
        db = self._build_db_for_parse(account_docs=[])
        app = _build_app(user)

        csv_bytes = self._csv_content.encode("utf-8")

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).post(
                "/api/finance/referral-mrr/parse-csv",
                data={
                    "linking_column": "BAN",
                    "amount_column": "Revenue",
                    "period": "2026-01",
                },
                files={"file": ("test.csv", csv_bytes, "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_rows"] == 2
        assert len(data["matched"]) == 0
        assert len(data["unmatched"]) == 2
        assert data["commissions_created"] == 0

    def test_parse_perm_denied(self):
        user = _make_user(permissions=[])
        db = self._build_db_for_parse()
        app = _build_app(user)

        csv_bytes = self._csv_content.encode("utf-8")

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).post(
                "/api/finance/referral-mrr/parse-csv",
                data={
                    "linking_column": "BAN",
                    "amount_column": "Revenue",
                    "period": "2026-01",
                },
                files={"file": ("test.csv", csv_bytes, "text/csv")},
            )

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Commit commissions tests
# ---------------------------------------------------------------------------

class TestCommitCommissions:
    _payload = {
        "rows": [
            {
                "account_number": "1234567890",
                "deal_id": "deal_abc",
                "deal_name": "Acme Corp",
                "partner_name": "AT&T",
                "amount": 500.0,
                "linking_field_name": "BAN",
                "raw_data": {"BAN": "1234567890", "Revenue": "500.00"},
            }
        ],
        "period": "2026-01",
    }

    def _build_db(self):
        ref_coll = _mock_collection()
        local_coll = _mock_collection()
        commissions_coll = _mock_collection(docs=[])
        commissions_coll.find_one = AsyncMock(return_value=None)  # no plan

        return _mock_db(
            {
                "referral_commissions": ref_coll,
                "local_commissions": local_coll,
                "commissions": commissions_coll,
            }
        )

    def test_commit_happy_path(self):
        user = _make_user()
        db = self._build_db()
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None), \
             patch(f"{ROUTE_MODULE}.crud_envelope", side_effect=_fake_envelope):
            resp = TestClient(app).post(
                "/api/finance/referral-mrr/commit-commissions", json=self._payload
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "referral_records" in data
        assert "user_commissions" in data
        assert len(data["referral_records"]) == 1

    def test_commit_perm_denied(self):
        user = _make_user(permissions=[])
        db = self._build_db()
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).post(
                "/api/finance/referral-mrr/commit-commissions", json=self._payload
            )

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Referral commissions list tests
# ---------------------------------------------------------------------------

class TestListReferralCommissions:
    def test_list_empty(self):
        user = _make_user()
        coll = _mock_collection(docs=[])
        db = _mock_db({"referral_commissions": coll})
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).get("/api/finance/referral-mrr/commissions")

        assert resp.status_code == 200
        data = resp.json()
        assert data["commissions"] == []
        assert data["total"] == 0

    def test_list_populated(self):
        user = _make_user()
        doc = _make_referral_commission_doc()
        coll = _mock_collection(docs=[doc])
        db = _mock_db({"referral_commissions": coll})
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).get("/api/finance/referral-mrr/commissions")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["commissions"]) == 1
        assert data["commissions"][0]["record_id"] == "refmrr_abc"

    def test_list_with_period_filter(self):
        user = _make_user()
        coll = _mock_collection(docs=[])
        db = _mock_db({"referral_commissions": coll})
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).get(
                "/api/finance/referral-mrr/commissions?period=2026-01"
            )

        assert resp.status_code == 200

    def test_list_perm_denied(self):
        user = _make_user(permissions=[])
        db = _mock_db()
        app = _build_app(user)

        with patch(f"{ROUTE_MODULE}.get_db", return_value=db), \
             patch(f"{AI_AUTHZ_MODULE}.require_ai_permission", return_value=lambda: None):
            resp = TestClient(app).get("/api/finance/referral-mrr/commissions")

        assert resp.status_code == 403
