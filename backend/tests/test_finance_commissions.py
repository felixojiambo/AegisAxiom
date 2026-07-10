"""Tests for backend/routes/finance_commissions.py.

Uses FastAPI's TestClient with fully mocked DB and auth — no live Mongo required.

Coverage:
  - GET /finance/commissions/overview  (200 shape, 403 denied)
  - GET /finance/commissions           (200 items, 403 denied)
  - PUT /finance/commissions/{id}      (200 happy, 400 no fields, 404 not found, 403 denied)
  - DELETE /finance/commissions/bulk   (200 happy, 403 denied)
  - DELETE /finance/commissions/{id}   (200 happy, 404 not found, 403 denied)
  - GET /finance/zoho-commissions      (200 shape, 403 denied)
  - GET /finance/users-summary         (200 shape, 403 denied)
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

def _make_user(permissions=None):
    """Build a fake user SimpleNamespace. Full perms by default."""
    perms = permissions if permissions is not None else [
        "finance.commissions.view",
        "finance.commissions.edit",
    ]
    return SimpleNamespace(
        user_id="u_001",
        email="tester@aegisaxiom.io",
        role="finance_admin",
        permissions=perms,
    )


def _build_app(fake_user):
    from routes.finance_commissions import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/finance")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return app


def _mock_collection(docs=None, deleted_count=1, matched_count=1):
    """Return a mock MongoDB collection with sensible async defaults."""
    docs = docs or []
    coll = MagicMock()
    coll.count_documents = AsyncMock(return_value=len(docs))
    coll.find_one = AsyncMock(return_value=docs[0] if docs else None)
    coll.delete_one = AsyncMock(
        return_value=MagicMock(deleted_count=deleted_count)
    )
    coll.delete_many = AsyncMock(
        return_value=MagicMock(deleted_count=deleted_count)
    )
    coll.update_one = AsyncMock(
        return_value=MagicMock(matched_count=matched_count, modified_count=matched_count)
    )

    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=docs)
    coll.find = MagicMock(return_value=cursor)
    return coll


def _mock_db(
    commissions=None,
    local_commissions=None,
    zoho_commissions=None,
    invoices=None,
    ar_invoices=None,
    users=None,
):
    """Build a mock db object with configurable collections."""
    comms_coll = _mock_collection(commissions or [])
    local_coll = _mock_collection(local_commissions or [])
    zoho_coll = _mock_collection(zoho_commissions or [])
    inv_coll = _mock_collection(invoices or [])
    ar_coll = _mock_collection(ar_invoices or [])
    users_coll = _mock_collection(users or [])

    db = MagicMock()
    db.commissions = comms_coll
    db.local_commissions = local_coll
    db.zoho_commissions = zoho_coll
    db.invoices = inv_coll
    db.ar_invoices = ar_coll
    db.users = users_coll
    return db, comms_coll, local_coll, zoho_coll, users_coll


@asynccontextmanager
async def _fake_envelope(*args, **kwargs):
    ctx = MagicMock()
    ctx.set_after = MagicMock()
    yield ctx


# A minimal commission record (no `tiers` field so it's not mistaken for a plan)
_COMM_DOC = {
    "commission_id": "comm_abc123",
    "user_id": "u_sales_001",
    "user_name": "Alice Sales",
    "deal_name": "Acme Corp Q1",
    "commission_type": "sales",
    "commission_rate": 0.05,
    "amount_owed": 5000.0,
    "amount_paid": 2500.0,
    "potential_amount": 5000.0,
    "status": "partial",
    "period": "2026-Q1",
    "paid_date": "2026-03-15",
    "notes": "",
    "created_at": "2026-01-10T00:00:00+00:00",
}

_ZOHO_DOC = {
    "zoho_commission_id": "zoho_001",
    "sales_rep_name": "Bob Rep",
    "agent_of_records_name": "Charlie Agent",
    "agent_commission": 1000.0,
    "agent_commission_status": "paid",
    "sales_rep_commission": 750.0,
    "sales_rep_commission_status": "paid",
    "dir_commission": 300.0,
    "payment_date": "2026-03-01",
    "synced_at": "2026-04-01T10:00:00+00:00",
}

_USER_DOC = {
    "user_id": "u_sales_001",
    "name": "Alice Sales",
    "email": "alice@aegisaxiom.io",
    "role": "sales_rep",
}


# ---------------------------------------------------------------------------
# Shared patch context for bypassing middleware
# ---------------------------------------------------------------------------

def _patches(db):
    return [
        patch("routes.finance_commissions.get_db", return_value=db),
        patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None),
    ]


# ---------------------------------------------------------------------------
# GET /finance/commissions/overview
# ---------------------------------------------------------------------------

class TestCommissionsOverview:
    def test_overview_200_correct_shape(self):
        user = _make_user()
        db, *_ = _mock_db(
            commissions=[_COMM_DOC],
            zoho_commissions=[_ZOHO_DOC],
        )
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/finance/commissions/overview")

        assert resp.status_code == 200
        data = resp.json()

        # Top-level keys
        assert "commissions" in data
        assert "zoho_commissions" in data
        assert "invoices" in data

        comms = data["commissions"]
        assert comms["total_owed"] == 5000.0
        assert comms["total_paid"] == 2500.0
        assert comms["total_potential"] == 5000.0
        assert comms["count"] == 1
        assert "by_status" in comms
        assert "by_user" in comms

        zoho = data["zoho_commissions"]
        assert zoho["agent_total"] == 1000.0
        assert zoho["rep_total"] == 750.0
        assert zoho["dir_total"] == 300.0
        assert zoho["count"] == 1
        assert len(zoho["by_rep"]) == 1

        inv = data["invoices"]
        assert "total" in inv
        assert "paid" in inv
        assert "outstanding" in inv

    def test_overview_403_no_perm(self):
        user = _make_user(permissions=[])
        db, *_ = _mock_db()
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/finance/commissions/overview")

        assert resp.status_code == 403

    def test_overview_passes_with_finance_access(self):
        """finance.access is an admin bypass — should be accepted."""
        user = _make_user(permissions=["finance.access"])
        db, *_ = _mock_db()
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/finance/commissions/overview")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /finance/commissions
# ---------------------------------------------------------------------------

class TestListCommissions:
    def test_list_200_shape(self):
        user = _make_user()
        db, *_ = _mock_db(commissions=[_COMM_DOC])
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/finance/commissions")

        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "skip" in data
        assert "limit" in data
        assert len(data["items"]) == 1
        assert data["items"][0]["commission_id"] == "comm_abc123"

    def test_list_empty(self):
        user = _make_user()
        db, *_ = _mock_db(commissions=[])
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/finance/commissions?skip=0&limit=10")

        assert resp.status_code == 200
        assert resp.json()["items"] == []
        assert resp.json()["total"] == 0

    def test_list_403_no_perm(self):
        user = _make_user(permissions=[])
        db, *_ = _mock_db()
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/finance/commissions")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /finance/commissions/{commission_id}
# ---------------------------------------------------------------------------

class TestUpdateCommission:
    def test_update_200_happy_path(self):
        user = _make_user()
        db, comms_coll, local_coll, *_ = _mock_db(commissions=[_COMM_DOC])
        comms_coll.find_one = AsyncMock(return_value=_COMM_DOC)
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_commissions.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.put(
                "/finance/commissions/comm_abc123",
                json={"status": "paid", "notes": "Paid via ACH"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["commission_id"] == "comm_abc123"
        assert "status" in data["updated"]
        assert "notes" in data["updated"]

    def test_update_400_no_valid_fields(self):
        user = _make_user()
        db, comms_coll, *_ = _mock_db(commissions=[_COMM_DOC])
        comms_coll.find_one = AsyncMock(return_value=_COMM_DOC)
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.put(
                "/finance/commissions/comm_abc123",
                json={"unknown_field": "value"},
            )

        assert resp.status_code == 400

    def test_update_404_not_found(self):
        user = _make_user()
        db, comms_coll, local_coll, *_ = _mock_db()
        comms_coll.find_one = AsyncMock(return_value=None)
        local_coll.find_one = AsyncMock(return_value=None)
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.put(
                "/finance/commissions/nonexistent",
                json={"status": "paid"},
            )

        assert resp.status_code == 404

    def test_update_403_no_edit_perm(self):
        user = _make_user(permissions=["finance.commissions.view"])
        db, *_ = _mock_db()
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.put(
                "/finance/commissions/comm_abc123",
                json={"status": "paid"},
            )

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /finance/commissions/bulk
# ---------------------------------------------------------------------------

class TestBulkDeleteCommissions:
    def test_bulk_delete_200_happy_path(self):
        user = _make_user()
        db, *_ = _mock_db()
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_commissions.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.request(
                "DELETE",
                "/finance/commissions/bulk",
                json=["comm_abc123", "comm_def456"],
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data
        assert "deleted_from_main" in data
        assert "deleted_from_local" in data

    def test_bulk_delete_403_no_perm(self):
        user = _make_user(permissions=["finance.commissions.view"])
        db, *_ = _mock_db()
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.request(
                "DELETE",
                "/finance/commissions/bulk",
                json=["comm_abc123"],
            )

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /finance/commissions/{commission_id}
# ---------------------------------------------------------------------------

class TestDeleteCommission:
    def test_delete_200_happy_path(self):
        user = _make_user()
        db, comms_coll, local_coll, *_ = _mock_db(commissions=[_COMM_DOC])
        comms_coll.find_one = AsyncMock(return_value=_COMM_DOC)
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_commissions.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.delete("/finance/commissions/comm_abc123")

        assert resp.status_code == 200
        data = resp.json()
        assert data["commission_id"] == "comm_abc123"
        assert "deleted_from_main" in data
        assert "deleted_from_local" in data

    def test_delete_404_not_found(self):
        user = _make_user()
        db, comms_coll, local_coll, *_ = _mock_db()
        comms_coll.find_one = AsyncMock(return_value=None)
        local_coll.find_one = AsyncMock(return_value=None)
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.delete("/finance/commissions/nonexistent")

        assert resp.status_code == 404

    def test_delete_403_no_perm(self):
        user = _make_user(permissions=["finance.commissions.view"])
        db, *_ = _mock_db()
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.delete("/finance/commissions/comm_abc123")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /finance/zoho-commissions
# ---------------------------------------------------------------------------

class TestZohoCommissions:
    def test_list_200_shape(self):
        user = _make_user()
        db, *_ = _mock_db(zoho_commissions=[_ZOHO_DOC])
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/finance/zoho-commissions")

        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert len(data["items"]) == 1
        assert data["items"][0]["zoho_commission_id"] == "zoho_001"

    def test_list_403_no_perm(self):
        user = _make_user(permissions=[])
        db, *_ = _mock_db()
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/finance/zoho-commissions")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /finance/users-summary
# ---------------------------------------------------------------------------

class TestUsersSummary:
    def test_list_200_shape(self):
        user = _make_user()
        db, *_ = _mock_db(users=[_USER_DOC])
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/finance/users-summary")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["user_id"] == "u_sales_001"
        assert data[0]["role"] == "sales_rep"

    def test_list_403_no_perm(self):
        user = _make_user(permissions=[])
        db, *_ = _mock_db()
        app = _build_app(user)

        with patch("routes.finance_commissions.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/finance/users-summary")

        assert resp.status_code == 403
