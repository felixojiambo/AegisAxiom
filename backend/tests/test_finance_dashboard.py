"""Tests for backend/routes/finance_dashboard.py.

Uses FastAPI's TestClient with fully mocked DB and auth — no live Mongo required.
Covers: list (empty + populated), create (happy + perm denied),
update (happy + 404), delete (happy + 404).
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
        "finance.dashboard.view",
        "finance.dashboard.create",
        "finance.dashboard.edit",
        "finance.dashboard.delete",
    ]
    return SimpleNamespace(
        user_id=user_id,
        email=email,
        role="finance_admin",
        permissions=perms,
    )


def _make_dash_doc(dashboard_id="fcc_dash_001", owner_user_id="u_001"):
    return {
        "dashboard_id": dashboard_id,
        "name": "Revenue Overview",
        "order": 0,
        "filters": {},
        "owner_user_id": owner_user_id,
        "created_at": "2026-05-04T12:00:00+00:00",
        "updated_at": "2026-05-04T12:00:00+00:00",
    }


def _build_app(fake_user, fake_db):
    from routes.finance_dashboard import router
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

class TestListDashboards:
    def test_list_empty(self):
        user = _make_user()
        db, _ = _mock_db(docs=[])
        app, _ = _build_app(user, db)

        with patch("routes.finance_dashboard.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/api/finance/workspace/dashboards")

        assert resp.status_code == 200
        data = resp.json()
        assert data["rows"] == []
        assert data["total"] == 0

    def test_list_populated(self):
        user = _make_user()
        doc = _make_dash_doc()
        db, _ = _mock_db(docs=[doc])
        app, _ = _build_app(user, db)

        with patch("routes.finance_dashboard.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/api/finance/workspace/dashboards")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["rows"]) == 1
        assert data["rows"][0]["dashboard_id"] == "fcc_dash_001"

    def test_list_response_includes_id_alias_and_widgets(self):
        """Frontend WorkspaceTab.js reads `d.id` and `d.widgets` — the list
        response must surface both. Without this the widget grid was always
        empty after Add Widget (Ryan's MVP bug, May 2026 finance call)."""
        user = _make_user()
        doc = _make_dash_doc()
        db, _ = _mock_db(docs=[doc])
        app, _ = _build_app(user, db)

        with patch("routes.finance_dashboard.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/api/finance/workspace/dashboards")

        assert resp.status_code == 200
        row = resp.json()["rows"][0]
        # `id` mirrors `dashboard_id` so frontend `setActiveId(d.id)` works
        assert row["id"] == row["dashboard_id"]
        # widgets always present (possibly empty) so `active.widgets.length` is safe
        assert "widgets" in row

    def test_list_perm_denied(self):
        user = _make_user(permissions=[])
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        with patch("routes.finance_dashboard.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.get("/api/finance/workspace/dashboards")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Create tests
# ---------------------------------------------------------------------------

class TestCreateDashboard:
    def _valid_payload(self):
        return {"name": "Cash Flow Monitor", "order": 1, "filters": {}}

    def test_create_happy_path(self):
        user = _make_user()
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        with patch("routes.finance_dashboard.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_dashboard.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.post("/api/finance/workspace/dashboards", json=self._valid_payload())

        assert resp.status_code == 201
        data = resp.json()
        assert data["dashboard_id"].startswith("fcc_dash_")
        assert data["name"] == "Cash Flow Monitor"
        assert data["owner_user_id"] == "u_001"
        # New contract: response carries the `id` alias and an empty widgets
        # list so `setActiveId(created.id)` and `active.widgets` work.
        assert data["id"] == data["dashboard_id"]
        assert data["widgets"] == []

    def test_create_perm_denied(self):
        user = _make_user(permissions=[])
        db, _ = _mock_db()
        app, _ = _build_app(user, db)

        with patch("routes.finance_dashboard.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post("/api/finance/workspace/dashboards", json=self._valid_payload())

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Update tests
# ---------------------------------------------------------------------------

class TestUpdateDashboard:
    def test_update_happy_path(self):
        user = _make_user()
        doc = _make_dash_doc()
        db, coll = _mock_db(docs=[doc])
        coll.find_one = AsyncMock(return_value=doc)
        app, _ = _build_app(user, db)

        with patch("routes.finance_dashboard.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_dashboard.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.patch(
                "/api/finance/workspace/dashboards/fcc_dash_001",
                json={"name": "Renamed Dashboard"},
            )

        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed Dashboard"

    def test_update_not_found(self):
        user = _make_user()
        db, coll = _mock_db()
        coll.find_one = AsyncMock(return_value=None)
        app, _ = _build_app(user, db)

        with patch("routes.finance_dashboard.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.patch(
                "/api/finance/workspace/dashboards/nonexistent",
                json={"name": "x"},
            )

        assert resp.status_code == 404

    def test_update_other_users_dashboard_denied(self):
        """Owner check: editing someone else's dashboard without admin.manage → 403."""
        user = _make_user(user_id="u_999")
        doc = _make_dash_doc(owner_user_id="u_001")  # different owner
        db, coll = _mock_db(docs=[doc])
        coll.find_one = AsyncMock(return_value=doc)
        app, _ = _build_app(user, db)

        with patch("routes.finance_dashboard.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.patch(
                "/api/finance/workspace/dashboards/fcc_dash_001",
                json={"name": "Steal"},
            )

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Delete tests
# ---------------------------------------------------------------------------

class TestDeleteDashboard:
    def test_delete_happy_path(self):
        user = _make_user()
        doc = _make_dash_doc()
        db, coll = _mock_db(docs=[doc])
        coll.find_one = AsyncMock(return_value=doc)
        app, _ = _build_app(user, db)

        with patch("routes.finance_dashboard.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_dashboard.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.delete("/api/finance/workspace/dashboards/fcc_dash_001")

        assert resp.status_code == 204

    def test_delete_not_found(self):
        user = _make_user()
        db, coll = _mock_db()
        coll.find_one = AsyncMock(return_value=None)
        app, _ = _build_app(user, db)

        with patch("routes.finance_dashboard.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.delete("/api/finance/workspace/dashboards/nonexistent")

        assert resp.status_code == 404
