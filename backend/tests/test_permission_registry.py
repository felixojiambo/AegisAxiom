"""
Permission Registry Tests

Verifies the dynamic permission registry behaves correctly:
- System permissions are seeded on startup and discoverable via the API
- Role creation/update/clone validate against the registry, not the
  hardcoded ALL_PERMISSIONS list
- Filters on the /api/roles/permissions endpoint work
"""
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
ADMIN_EMAIL = "support@simplimeta.io"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@123")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def admin_session():
    s = requests.Session()
    resp = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    return s


# ---------------------------------------------------------------------------
# /api/roles/permissions surfaces the registry
# ---------------------------------------------------------------------------

class TestListPermissionsEndpoint:
    """GET /api/roles/permissions should read from the registry collection."""

    def test_returns_seeded_system_permissions(self, admin_session):
        """The registry seed runs on startup; every system perm should be discoverable."""
        resp = admin_session.get(f"{BASE_URL}/api/roles/permissions")
        assert resp.status_code == 200, resp.text
        perms = resp.json()
        assert isinstance(perms, list)
        assert len(perms) >= 100, f"Expected >=100 system permissions, got {len(perms)}"

    def test_returns_well_formed_documents(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/roles/permissions")
        perms = resp.json()
        sample = perms[0]
        for field in ("key", "group", "label", "source", "is_active"):
            assert field in sample, f"Missing field {field} in permission doc"
        assert sample["is_active"] is True
        assert sample["source"] == "system"

    def test_filter_by_source(self, admin_session):
        """Filtering by source=system returns only system permissions."""
        resp = admin_session.get(f"{BASE_URL}/api/roles/permissions", params={"source": "system"})
        assert resp.status_code == 200
        perms = resp.json()
        assert all(p["source"] == "system" for p in perms)

    def test_filter_by_group(self, admin_session):
        """Filtering by group returns only that group's permissions."""
        resp = admin_session.get(f"{BASE_URL}/api/roles/permissions", params={"group": "Roles"})
        assert resp.status_code == 200
        perms = resp.json()
        assert len(perms) > 0
        assert all(p["group"] == "Roles" for p in perms)


# ---------------------------------------------------------------------------
# Role validation reads from the registry
# ---------------------------------------------------------------------------

class TestRoleValidationUsesRegistry:
    """Role creation/update should validate permission keys against the registry."""

    def test_create_role_rejects_unknown_key(self, admin_session):
        """A made-up permission key should be rejected with 400."""
        bogus_key = f"made_up_app.entity.action_{uuid.uuid4().hex[:6]}"
        resp = admin_session.post(f"{BASE_URL}/api/roles", json={
            "name": f"test_unknown_{uuid.uuid4().hex[:6]}",
            "display_name": "Unknown Key Test",
            "permissions": [bogus_key],
            "color": "#444444",
        })
        assert resp.status_code == 400, f"Should reject unknown key, got {resp.status_code}: {resp.text}"
        assert "Invalid permission keys" in resp.text or "invalid" in resp.text.lower()

    def test_create_role_accepts_seeded_keys(self, admin_session):
        """Any key from the registry should be accepted."""
        unique_name = f"test_registry_{uuid.uuid4().hex[:8]}"
        resp = admin_session.post(f"{BASE_URL}/api/roles", json={
            "name": unique_name,
            "display_name": "Registry Validation Test",
            "permissions": ["deals.view.own", "contacts.view.own"],
            "color": "#555555",
        })
        assert resp.status_code in (200, 201), f"Should accept seeded keys: {resp.text}"
        role_id = resp.json()["role_id"]
        admin_session.delete(f"{BASE_URL}/api/roles/{role_id}")

    def test_update_role_rejects_unknown_key(self, admin_session):
        """Updating a role to include an unknown permission should fail."""
        unique_name = f"test_update_{uuid.uuid4().hex[:8]}"
        create = admin_session.post(f"{BASE_URL}/api/roles", json={
            "name": unique_name,
            "display_name": "Update Test",
            "permissions": ["deals.view.own"],
            "color": "#666666",
        })
        assert create.status_code in (200, 201)
        role_id = create.json()["role_id"]
        try:
            bogus = f"phony.permission_{uuid.uuid4().hex[:6]}"
            update = admin_session.put(f"{BASE_URL}/api/roles/{role_id}", json={
                "permissions": ["deals.view.own", bogus],
            })
            assert update.status_code == 400, f"Should reject unknown key on update: {update.text}"
        finally:
            admin_session.delete(f"{BASE_URL}/api/roles/{role_id}")
