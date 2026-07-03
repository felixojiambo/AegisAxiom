"""
AI Runtime Authorization Enforcement Tests (Wave 1A + 2C).

Exercises middleware/ai_authz.py:
  - require_ai_permission() as a FastAPI dependency
  - get_flattened_ai_permissions() with caching
  - _ensure_backing_user() from routes/ai_employees.py

Tests are self-contained: DB is fully mocked; no live Mongo, no live server.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, call

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ai_user(employee_id="ai_emp_001", user_id="ai_user_001", email="ai_emp_001@ai.aegisaxiom.internal"):
    """Simulate a user flagged as is_ai_employee=True."""
    return SimpleNamespace(
        user_id=user_id,
        email=email,
        role="ai_employee",
        is_ai_employee=True,
        ai_employee_id=employee_id,
        permissions=[],
    )


def _make_human_user(user_id="human_001", email="human@example.com"):
    """Simulate a regular human user (no is_ai_employee flag)."""
    return SimpleNamespace(
        user_id=user_id,
        email=email,
        role="sales_rep",
        # NOTE: deliberately no is_ai_employee attribute — tests the getattr default
        permissions=["crm.contacts.view"],
    )


def _make_employee_doc(employee_id="ai_emp_001", app_access=None):
    return {
        "employee_id": employee_id,
        "app_access": app_access or [],
        "user_id": "ai_user_001",
    }


def _build_app(permission_key: str, fake_user, fake_db):
    """Build a minimal FastAPI app with one route gated by require_ai_permission."""
    from middleware.ai_authz import require_ai_permission

    app = FastAPI()

    @app.get(
        "/test-route",
        dependencies=[Depends(require_ai_permission(permission_key))],
    )
    async def _handler():
        return {"ok": True}

    # Override get_current_user so the test controls who's calling
    from auth_helper import get_current_user
    app.dependency_overrides[get_current_user] = lambda: fake_user

    # Patch get_db() to return our fake db for this request
    return app, fake_db


# ---------------------------------------------------------------------------
# AI with no app_access → 403 + audit log entry
# ---------------------------------------------------------------------------

class TestAINoAppAccess:
    @pytest.mark.asyncio
    async def test_ai_with_no_app_access_returns_403(self):
        ai_user = _make_ai_user()

        # Employee doc with empty app_access
        employee_doc = _make_employee_doc(app_access=[])

        mock_db = MagicMock()
        mock_db.ai_employees.find_one = AsyncMock(return_value=employee_doc)
        mock_db.users.find_one = AsyncMock(return_value={"extra_permissions": []})
        mock_db.audit_logs.insert_one = AsyncMock()

        from middleware.ai_authz import require_ai_permission
        from auth_helper import get_current_user

        app = FastAPI()

        @app.get("/protected", dependencies=[Depends(require_ai_permission("crm.contacts.view"))])
        async def _handler():
            return {"ok": True}

        app.dependency_overrides[get_current_user] = lambda: ai_user

        with patch("middleware.ai_authz.get_db", return_value=mock_db):
            with patch("services.security_logger.get_db", return_value=mock_db):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.get("/protected")

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_ai_denied_writes_authz_failed_audit_log(self):
        ai_user = _make_ai_user()
        employee_doc = _make_employee_doc(app_access=[])
        audit_writes = []

        mock_db = MagicMock()
        mock_db.ai_employees.find_one = AsyncMock(return_value=employee_doc)
        mock_db.users.find_one = AsyncMock(return_value={"extra_permissions": []})
        mock_db.audit_logs.insert_one = AsyncMock(side_effect=lambda doc: audit_writes.append(doc))

        from middleware.ai_authz import require_ai_permission
        from auth_helper import get_current_user

        app = FastAPI()

        @app.get("/protected", dependencies=[Depends(require_ai_permission("crm.contacts.view"))])
        async def _handler():
            return {"ok": True}

        app.dependency_overrides[get_current_user] = lambda: ai_user

        with patch("middleware.ai_authz.get_db", return_value=mock_db):
            with patch("services.security_logger.get_db", return_value=mock_db):
                client = TestClient(app, raise_server_exceptions=False)
                client.get("/protected")

        # Audit log must have been written with authz_failed
        assert len(audit_writes) >= 1
        assert any(w.get("event_type") == "authz_failed" for w in audit_writes), (
            f"Expected authz_failed in audit writes, got: {audit_writes}"
        )


# ---------------------------------------------------------------------------
# AI with matching permission → 200
# ---------------------------------------------------------------------------

class TestAIWithPermission:
    @pytest.mark.asyncio
    async def test_ai_with_view_perm_returns_200_on_view_route(self):
        ai_user = _make_ai_user()
        employee_doc = _make_employee_doc(app_access=[
            {"app_slug": "crm", "permissions": ["crm.contacts.view"]},
        ])

        mock_db = MagicMock()
        mock_db.ai_employees.find_one = AsyncMock(return_value=employee_doc)
        mock_db.users.find_one = AsyncMock(return_value={"extra_permissions": []})

        from middleware.ai_authz import require_ai_permission
        from auth_helper import get_current_user

        app = FastAPI()

        @app.get("/protected", dependencies=[Depends(require_ai_permission("crm.contacts.view"))])
        async def _handler():
            return {"ok": True}

        app.dependency_overrides[get_current_user] = lambda: ai_user

        with patch("middleware.ai_authz.get_db", return_value=mock_db):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/protected")

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_ai_with_view_perm_denied_on_create_route(self):
        ai_user = _make_ai_user()
        employee_doc = _make_employee_doc(app_access=[
            {"app_slug": "crm", "permissions": ["crm.contacts.view"]},
        ])

        mock_db = MagicMock()
        mock_db.ai_employees.find_one = AsyncMock(return_value=employee_doc)
        mock_db.users.find_one = AsyncMock(return_value={"extra_permissions": []})
        mock_db.audit_logs.insert_one = AsyncMock()

        from middleware.ai_authz import require_ai_permission
        from auth_helper import get_current_user

        app = FastAPI()

        @app.get("/protected", dependencies=[Depends(require_ai_permission("crm.contacts.create"))])
        async def _handler():
            return {"ok": True}

        app.dependency_overrides[get_current_user] = lambda: ai_user

        with patch("middleware.ai_authz.get_db", return_value=mock_db):
            with patch("services.security_logger.get_db", return_value=mock_db):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.get("/protected")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# AI with wildcard → passes any perm
# ---------------------------------------------------------------------------

class TestAIWildcard:
    @pytest.mark.asyncio
    async def test_wildcard_in_app_access_passes_any_perm(self):
        ai_user = _make_ai_user()
        employee_doc = _make_employee_doc(app_access=[
            {"app_slug": "all", "permissions": ["*"]},
        ])

        mock_db = MagicMock()
        mock_db.ai_employees.find_one = AsyncMock(return_value=employee_doc)
        mock_db.users.find_one = AsyncMock(return_value={"extra_permissions": []})

        from middleware.ai_authz import require_ai_permission
        from auth_helper import get_current_user

        app = FastAPI()

        @app.get("/protected", dependencies=[Depends(require_ai_permission("crm.contacts.delete.all"))])
        async def _handler():
            return {"ok": True}

        app.dependency_overrides[get_current_user] = lambda: ai_user

        with patch("middleware.ai_authz.get_db", return_value=mock_db):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/protected")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Human user → no-op (route always passes)
# ---------------------------------------------------------------------------

class TestHumanUserIsNoop:
    @pytest.mark.asyncio
    async def test_human_user_bypasses_ai_perm_check(self):
        human_user = _make_human_user()

        from middleware.ai_authz import require_ai_permission
        from auth_helper import get_current_user

        app = FastAPI()
        audit_writes = []

        @app.get("/protected", dependencies=[Depends(require_ai_permission("crm.contacts.delete.all"))])
        async def _handler():
            return {"ok": True}

        app.dependency_overrides[get_current_user] = lambda: human_user

        # Even with a mock_db that denies everything, the human path never touches DB
        with patch("middleware.ai_authz.get_db", side_effect=RuntimeError("should not call DB for human")):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/protected")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Per-request cache: DB hit exactly once for multiple permission checks
# ---------------------------------------------------------------------------

class TestPerRequestCache:
    @pytest.mark.asyncio
    async def test_two_checks_in_same_request_hit_db_once(self):
        """Both dependencies in the same request share the cache — DB fetched once."""
        ai_user = _make_ai_user()
        employee_doc = _make_employee_doc(app_access=[
            {"app_slug": "crm", "permissions": ["crm.contacts.view", "crm.contacts.edit"]},
        ])

        find_one_call_count = 0

        async def counting_find_one(query, projection=None):
            nonlocal find_one_call_count
            if "employee_id" in query:
                find_one_call_count += 1
            return employee_doc

        mock_db = MagicMock()
        mock_db.ai_employees.find_one = counting_find_one
        mock_db.users.find_one = AsyncMock(return_value={"extra_permissions": []})

        from middleware.ai_authz import require_ai_permission
        from auth_helper import get_current_user

        app = FastAPI()

        @app.get(
            "/protected",
            dependencies=[
                Depends(require_ai_permission("crm.contacts.view")),
                Depends(require_ai_permission("crm.contacts.edit")),
            ],
        )
        async def _handler():
            return {"ok": True}

        app.dependency_overrides[get_current_user] = lambda: ai_user

        with patch("middleware.ai_authz.get_db", return_value=mock_db):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/protected")

        assert resp.status_code == 200
        # Because FastAPI resolves each Depends(require_ai_permission(...)) independently
        # within the same request, the cache should keep the DB call count to 1.
        assert find_one_call_count == 1, (
            f"Expected DB lookup exactly once (cache), got {find_one_call_count}"
        )


# ---------------------------------------------------------------------------
# _ensure_backing_user creates users doc with role="ai_employee", is_system=True
# ---------------------------------------------------------------------------

class TestEnsureBackingUser:
    @pytest.mark.asyncio
    async def test_ensure_backing_user_creates_system_user(self):
        from routes.ai_employees import _ensure_backing_user

        inserted_docs = []

        async def mock_find_one(query, projection=None):
            return None  # not found — triggers creation

        async def mock_insert_one(doc):
            inserted_docs.append(doc)

        mock_db = MagicMock()
        mock_db.users.find_one = mock_find_one
        mock_db.users.insert_one = mock_insert_one

        user_id = await _ensure_backing_user(mock_db, "ai_emp_test_123", "Test Bot")

        assert user_id.startswith("ai_user_")
        assert len(inserted_docs) == 1
        doc = inserted_docs[0]
        assert doc["role"] == "ai_employee"
        assert doc["is_system"] is True
        assert doc["is_active"] is True
        assert doc["ai_employee_id"] == "ai_emp_test_123"
        assert doc["email"] == "ai_emp_test_123@ai.aegisaxiom.internal"

    @pytest.mark.asyncio
    async def test_ensure_backing_user_idempotent(self):
        """If backing user already exists, return existing user_id without inserting."""
        from routes.ai_employees import _ensure_backing_user

        existing_user_id = "ai_user_already_exists"
        insert_called = []

        async def mock_find_one(query, projection=None):
            return {"user_id": existing_user_id}

        async def mock_insert_one(doc):
            insert_called.append(doc)

        mock_db = MagicMock()
        mock_db.users.find_one = mock_find_one
        mock_db.users.insert_one = mock_insert_one

        result = await _ensure_backing_user(mock_db, "ai_emp_existing", "Existing Bot")

        assert result == existing_user_id
        assert len(insert_called) == 0, "Should not insert if user already exists"
