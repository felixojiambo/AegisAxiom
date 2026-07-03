"""Tests for backend/routes/finance_widget.py.

Uses FastAPI's TestClient with fully mocked DB and auth — no live Mongo required.
Covers: create (happy + perm denied + invalid size), update (happy + 404),
delete (happy + 404), reorder (happy + dashboard 404).
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
        "finance.widget.view",
        "finance.widget.create",
        "finance.widget.edit",
        "finance.widget.delete",
    ]
    return SimpleNamespace(
        user_id=user_id,
        email=email,
        role="finance_admin",
        permissions=perms,
    )


def _make_widget_doc(widget_id="fcc_widget_001", dashboard_id="fcc_dash_001"):
    return {
        "widget_id": widget_id,
        "dashboard_id": dashboard_id,
        "widget_type": "pnl_summary",
        "size": "medium",
        "position": 0,
        "config": {},
        "created_at": "2026-05-04T12:00:00+00:00",
        "updated_at": "2026-05-04T12:00:00+00:00",
    }


def _make_dash_doc(dashboard_id="fcc_dash_001"):
    return {
        "dashboard_id": dashboard_id,
        "name": "Revenue Overview",
        "owner_user_id": "u_001",
    }


def _build_app(fake_user, fake_db):
    from routes.finance_widget import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/finance")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return app, fake_db


def _mock_db(widget_docs=None, dash_doc=None):
    widget_docs = widget_docs or []

    widget_coll = MagicMock()
    widget_coll.count_documents = AsyncMock(return_value=len(widget_docs))
    widget_cursor = MagicMock()
    widget_cursor.sort = MagicMock(return_value=widget_cursor)
    widget_cursor.skip = MagicMock(return_value=widget_cursor)
    widget_cursor.limit = MagicMock(return_value=widget_cursor)
    widget_cursor.to_list = AsyncMock(return_value=widget_docs)
    widget_coll.find = MagicMock(return_value=widget_cursor)
    widget_coll.find_one = AsyncMock(return_value=widget_docs[0] if widget_docs else None)
    widget_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="fake_oid"))
    widget_coll.replace_one = AsyncMock(return_value=MagicMock(modified_count=1))
    widget_coll.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    widget_coll.bulk_write = AsyncMock(return_value=MagicMock(modified_count=2))

    dash_coll = MagicMock()
    dash_coll.find_one = AsyncMock(return_value=dash_doc)

    def _getitem(name):
        if name == "finance_widgets":
            return widget_coll
        if name == "finance_dashboards":
            return dash_coll
        return MagicMock()

    db = MagicMock()
    db.__getitem__ = MagicMock(side_effect=_getitem)
    return db, widget_coll, dash_coll


@asynccontextmanager
async def _fake_envelope(*args, **kwargs):
    ctx = MagicMock()
    ctx.set_after = MagicMock()
    yield ctx


# ---------------------------------------------------------------------------
# Create tests
# ---------------------------------------------------------------------------

class TestCreateWidget:
    def _valid_payload(self):
        return {"widget_type": "pnl_summary", "size": "medium", "config": {}}

    def test_create_happy_path(self):
        user = _make_user()
        db, _, _ = _mock_db(dash_doc=_make_dash_doc())
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_widget.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.post(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets",
                json=self._valid_payload(),
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["widget_id"].startswith("fcc_widget_")
        assert data["dashboard_id"] == "fcc_dash_001"
        assert data["widget_type"] == "pnl_summary"
        assert data["size"] == "medium"

    def test_create_perm_denied(self):
        user = _make_user(permissions=[])
        db, _, _ = _mock_db(dash_doc=_make_dash_doc())
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets",
                json=self._valid_payload(),
            )

        assert resp.status_code == 403

    def test_create_invalid_size(self):
        user = _make_user()
        db, _, _ = _mock_db(dash_doc=_make_dash_doc())
        app, _ = _build_app(user, db)

        payload = self._valid_payload()
        payload["size"] = "xl"

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets",
                json=payload,
            )

        assert resp.status_code == 422
        assert "size" in resp.json()["detail"]

    def test_create_dashboard_not_found(self):
        user = _make_user()
        db, _, dash_coll = _mock_db(dash_doc=None)
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post(
                "/api/finance/workspace/dashboards/nonexistent/widgets",
                json=self._valid_payload(),
            )

        assert resp.status_code == 404
        assert "Dashboard" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Update tests
# ---------------------------------------------------------------------------

class TestUpdateWidget:
    def test_update_happy_path(self):
        user = _make_user()
        doc = _make_widget_doc()
        db, widget_coll, _ = _mock_db(widget_docs=[doc])
        widget_coll.find_one = AsyncMock(return_value=doc)
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_widget.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.patch(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets/fcc_widget_001",
                json={"size": "large", "position": 2},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["size"] == "large"
        assert data["position"] == 2

    def test_update_not_found(self):
        user = _make_user()
        db, widget_coll, _ = _mock_db()
        widget_coll.find_one = AsyncMock(return_value=None)
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.patch(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets/nonexistent",
                json={"size": "small"},
            )

        assert resp.status_code == 404

    def test_update_invalid_size(self):
        user = _make_user()
        doc = _make_widget_doc()
        db, widget_coll, _ = _mock_db(widget_docs=[doc])
        widget_coll.find_one = AsyncMock(return_value=doc)
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.patch(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets/fcc_widget_001",
                json={"size": "xxl"},
            )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Delete tests
# ---------------------------------------------------------------------------

class TestDeleteWidget:
    def test_delete_happy_path(self):
        user = _make_user()
        doc = _make_widget_doc()
        db, widget_coll, _ = _mock_db(widget_docs=[doc])
        widget_coll.find_one = AsyncMock(return_value=doc)
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_widget.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.delete(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets/fcc_widget_001"
            )

        assert resp.status_code == 204

    def test_delete_not_found(self):
        user = _make_user()
        db, widget_coll, _ = _mock_db()
        widget_coll.find_one = AsyncMock(return_value=None)
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.delete(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets/nonexistent"
            )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Reorder tests
# ---------------------------------------------------------------------------

class TestReorderWidgets:
    def test_reorder_happy_path(self):
        user = _make_user()
        db, widget_coll, _ = _mock_db(dash_doc=_make_dash_doc())
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_widget.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.post(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets/reorder",
                json={"order": ["fcc_widget_002", "fcc_widget_001"]},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "reordered" in data
        widget_coll.bulk_write.assert_awaited_once()

    def test_reorder_dashboard_not_found(self):
        user = _make_user()
        db, _, dash_coll = _mock_db(dash_doc=None)
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post(
                "/api/finance/workspace/dashboards/nonexistent/widgets/reorder",
                json={"order": ["fcc_widget_001"]},
            )

        assert resp.status_code == 404

    def test_reorder_empty_order_returns_zero(self):
        user = _make_user()
        db, _, _ = _mock_db(dash_doc=_make_dash_doc())
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets/reorder",
                json={"order": []},
            )

        assert resp.status_code == 200
        assert resp.json()["reordered"] == 0


# ---------------------------------------------------------------------------
# Widget ownership tests (N3)
# ---------------------------------------------------------------------------

class TestWidgetOwnership:
    """User B cannot mutate widgets on User A's dashboard; admin can."""

    def _valid_payload(self):
        return {"widget_type": "pnl_summary", "size": "medium", "config": {}}

    def test_create_by_owner_succeeds(self):
        """Owner (u_001) creates a widget on their own dashboard → 201."""
        user = _make_user(user_id="u_001")
        dash = _make_dash_doc(dashboard_id="fcc_dash_001")  # owner_user_id = u_001
        db, _, _ = _mock_db(dash_doc=dash)
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_widget.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.post(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets",
                json=self._valid_payload(),
            )

        assert resp.status_code == 201

    def test_create_by_non_owner_denied(self):
        """User B (u_999) tries to add a widget to User A's dashboard → 403."""
        user = _make_user(user_id="u_999")
        dash = _make_dash_doc(dashboard_id="fcc_dash_001")  # owner_user_id = u_001
        db, _, _ = _mock_db(dash_doc=dash)
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets",
                json=self._valid_payload(),
            )

        assert resp.status_code == 403
        assert "own dashboards" in resp.json()["detail"]

    def test_create_by_admin_succeeds(self):
        """Admin (admin.manage perm) can create widgets on any dashboard → 201."""
        user = _make_user(
            user_id="u_admin",
            permissions=["finance.widget.create", "admin.manage"],
        )
        dash = _make_dash_doc(dashboard_id="fcc_dash_001")  # owner = u_001, not admin
        db, _, _ = _mock_db(dash_doc=dash)
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None), \
             patch("routes.finance_widget.crud_envelope", side_effect=_fake_envelope):
            client = TestClient(app)
            resp = client.post(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets",
                json=self._valid_payload(),
            )

        assert resp.status_code == 201

    def test_update_by_non_owner_denied(self):
        """User B cannot update a widget on User A's dashboard → 403."""
        user = _make_user(user_id="u_999")
        dash = _make_dash_doc(dashboard_id="fcc_dash_001")  # owner = u_001
        widget = _make_widget_doc()
        db, widget_coll, _ = _mock_db(widget_docs=[widget], dash_doc=dash)
        widget_coll.find_one = AsyncMock(return_value=widget)
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.patch(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets/fcc_widget_001",
                json={"size": "large"},
            )

        assert resp.status_code == 403

    def test_delete_by_non_owner_denied(self):
        """User B cannot delete a widget on User A's dashboard → 403."""
        user = _make_user(user_id="u_999")
        dash = _make_dash_doc(dashboard_id="fcc_dash_001")  # owner = u_001
        widget = _make_widget_doc()
        db, widget_coll, _ = _mock_db(widget_docs=[widget], dash_doc=dash)
        widget_coll.find_one = AsyncMock(return_value=widget)
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.delete(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets/fcc_widget_001"
            )

        assert resp.status_code == 403

    def test_reorder_by_non_owner_denied(self):
        """User B cannot reorder widgets on User A's dashboard → 403."""
        user = _make_user(user_id="u_999")
        dash = _make_dash_doc(dashboard_id="fcc_dash_001")  # owner = u_001
        db, _, _ = _mock_db(dash_doc=dash)
        app, _ = _build_app(user, db)

        with patch("routes.finance_widget.get_db", return_value=db), \
             patch("middleware.ai_authz.require_ai_permission", return_value=lambda: None):
            client = TestClient(app)
            resp = client.post(
                "/api/finance/workspace/dashboards/fcc_dash_001/widgets/reorder",
                json={"order": ["fcc_widget_001"]},
            )

        assert resp.status_code == 403
