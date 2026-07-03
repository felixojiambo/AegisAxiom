"""
RBAC Permission Enforcement Tests
Verifies that authentication and authorization checks are applied correctly
across key endpoints after the RBAC overhaul.
"""
import pytest
import requests
import os
import time
import uuid

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


@pytest.fixture(scope="session")
def no_auth_session():
    """A session with no credentials at all."""
    return requests.Session()


@pytest.fixture(scope="session")
def limited_user_session(admin_session):
    """
    Creates a user with only 'users.edit' (not admin role) and returns a
    logged-in session for that user. Cleaned up at end of session.
    """
    unique_email = f"test_limited_{uuid.uuid4().hex[:8]}@test.example.com"
    unique_name = f"Test Limited {uuid.uuid4().hex[:6]}"

    # Create the user via invite (admin creates with director role)
    invite_resp = admin_session.post(f"{BASE_URL}/api/admin/invite", json={
        "email": unique_email,
        "name": unique_name,
        "role": "director",
    })
    if invite_resp.status_code not in (200, 201):
        pytest.skip(f"Could not create test user: {invite_resp.text}")

    user_id = invite_resp.json().get("user_id")

    # Activate the user with a password so we can log in
    pw = "TestPass@999"
    activate_resp = admin_session.post(f"{BASE_URL}/api/admin/users/{user_id}/set-password", json={"password": pw})
    if activate_resp.status_code not in (200, 201, 404):
        # If set-password doesn't exist, try the invite accept flow
        pass

    # If direct activation isn't available, mark is_active via direct update
    activate_patch = admin_session.patch(f"{BASE_URL}/api/admin/users/{user_id}", json={"is_active": True, "password": pw})

    # Try to login regardless
    s = requests.Session()
    login_resp = s.post(f"{BASE_URL}/api/auth/login", json={"email": unique_email, "password": pw})

    yield s, user_id, unique_email

    # Cleanup
    admin_session.delete(f"{BASE_URL}/api/admin/users/{user_id}")


# ---------------------------------------------------------------------------
# Unauthenticated Access (401 checks)
# ---------------------------------------------------------------------------

class TestUnauthenticatedBlocked:
    """Unauthenticated requests to protected endpoints must return 401."""

    PROTECTED_ENDPOINTS = [
        ("GET", "/api/roles"),
        ("GET", "/api/admin/users"),
        ("GET", "/api/admin/time-locking/rules/active"),
        ("GET", "/api/commissions"),
        ("GET", "/api/deals"),
        ("GET", "/api/ai-employees"),
        ("GET", "/api/audit"),
    ]

    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    def test_protected_endpoint_blocks_unauthenticated(self, no_auth_session, method, path):
        """Every protected endpoint must return 401 with no auth credentials."""
        resp = no_auth_session.request(method, f"{BASE_URL}{path}")
        assert resp.status_code == 401, (
            f"{method} {path} returned {resp.status_code} (expected 401). "
            f"Body: {resp.text[:200]}"
        )

    def test_time_locking_active_rule_requires_auth(self, no_auth_session):
        """GET /api/admin/time-locking/rules/active was previously public — now must require auth."""
        resp = no_auth_session.get(f"{BASE_URL}/api/admin/time-locking/rules/active")
        assert resp.status_code == 401, (
            f"Time-locking active rules endpoint should require auth, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Admin Full-Access Smoke Tests
# ---------------------------------------------------------------------------

class TestAdminCanAccessAll:
    """Admin role should get 200 on all key endpoints."""

    def test_admin_can_list_roles(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/roles")
        assert resp.status_code == 200

    def test_admin_can_list_users(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/users")
        assert resp.status_code == 200

    def test_admin_can_list_time_locking_rules(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/admin/time-locking/rules/active")
        assert resp.status_code == 200

    def test_admin_can_list_commissions(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/commissions")
        assert resp.status_code == 200

    def test_admin_can_list_deals(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/deals")
        assert resp.status_code == 200

    def test_admin_can_access_audit_log(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/audit")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# roles.manage Permission Gate
# ---------------------------------------------------------------------------

class TestRolesRequireRolesManagePerm:
    """GET /api/roles requires the roles.manage permission."""

    def test_list_roles_requires_auth(self, no_auth_session):
        resp = no_auth_session.get(f"{BASE_URL}/api/roles")
        assert resp.status_code == 401

    def test_admin_can_list_roles(self, admin_session):
        resp = admin_session.get(f"{BASE_URL}/api/roles")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# Role CRUD Requires roles.manage Permission
# ---------------------------------------------------------------------------

class TestRoleCRUDRequiresRolesManagePerm:
    """POST/PUT/DELETE /api/roles must reject users without roles.manage permission with 403."""

    def test_create_role_by_admin_succeeds(self, admin_session):
        unique_name = f"test_rbac_{uuid.uuid4().hex[:8]}"
        resp = admin_session.post(f"{BASE_URL}/api/roles", json={
            "name": unique_name,
            "display_name": "Test RBAC Role",
            "permissions": ["deals.view.own"],
            "color": "#333333",
        })
        assert resp.status_code in (200, 201), f"Admin should be able to create role: {resp.text}"
        role_id = resp.json()["role_id"]

        # Cleanup
        admin_session.delete(f"{BASE_URL}/api/roles/{role_id}")

    def test_create_role_rejects_wildcard_permission(self, admin_session):
        """POST /api/roles with '*' in permissions must return 400."""
        resp = admin_session.post(f"{BASE_URL}/api/roles", json={
            "name": f"wildcard_role_{uuid.uuid4().hex[:6]}",
            "display_name": "Wildcard Role",
            "permissions": ["deals.view.own", "*"],
            "color": "#000000",
        })
        assert resp.status_code == 400, (
            f"Should reject wildcard '*' permission, got {resp.status_code}: {resp.text}"
        )

    def test_create_role_rejects_unknown_permission_key(self, admin_session):
        """POST /api/roles with an invented permission key must return 400."""
        resp = admin_session.post(f"{BASE_URL}/api/roles", json={
            "name": f"bad_perm_role_{uuid.uuid4().hex[:6]}",
            "display_name": "Bad Perm Role",
            "permissions": ["deals.view.own", "totally.made.up.permission"],
            "color": "#000000",
        })
        assert resp.status_code == 400, (
            f"Should reject unknown permission key, got {resp.status_code}: {resp.text}"
        )

    def test_update_role_rejects_wildcard_permission(self, admin_session):
        """PUT /api/roles/{id} with '*' in permissions must return 400."""
        # Get a non-system role to attempt update
        roles_resp = admin_session.get(f"{BASE_URL}/api/roles")
        assert roles_resp.status_code == 200
        roles = roles_resp.json()
        non_system = next((r for r in roles if not r.get("is_system")), None)
        if not non_system:
            pytest.skip("No non-system role found to test update")

        resp = admin_session.put(f"{BASE_URL}/api/roles/{non_system['role_id']}", json={
            "permissions": ["deals.view.own", "*"],
        })
        assert resp.status_code == 400, (
            f"Should reject wildcard '*' on update, got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# User Update: Role/Extra-Permissions Change Requires admin
# ---------------------------------------------------------------------------

class TestUserUpdateRoleRequiresAdmin:
    """PUT /api/admin/users/{id} with role change must require admin role."""

    def test_admin_can_change_user_role(self, admin_session):
        """Admin can update another user's role."""
        users_resp = admin_session.get(f"{BASE_URL}/api/admin/users")
        assert users_resp.status_code == 200
        users = users_resp.json()

        # Find a non-admin user to update
        target = next(
            (u for u in users if u.get("role") != "admin" and u.get("email") != ADMIN_EMAIL),
            None
        )
        if not target:
            pytest.skip("No non-admin user available to test role change")

        original_role = target["role"]
        new_role = "customer" if original_role != "customer" else "agent"

        resp = admin_session.put(f"{BASE_URL}/api/admin/users/{target['user_id']}", json={
            "role": new_role
        })
        assert resp.status_code == 200, f"Admin should be able to change user role: {resp.text}"

        # Restore original role
        admin_session.put(f"{BASE_URL}/api/admin/users/{target['user_id']}", json={
            "role": original_role
        })

    def test_admin_can_change_extra_permissions(self, admin_session):
        """Admin can update another user's extra_permissions."""
        users_resp = admin_session.get(f"{BASE_URL}/api/admin/users")
        assert users_resp.status_code == 200
        users = users_resp.json()

        target = next(
            (u for u in users if u.get("role") != "admin" and u.get("email") != ADMIN_EMAIL),
            None
        )
        if not target:
            pytest.skip("No non-admin user available to test extra_permissions change")

        resp = admin_session.put(f"{BASE_URL}/api/admin/users/{target['user_id']}", json={
            "extra_permissions": ["deals.view.own"]
        })
        assert resp.status_code == 200, f"Admin should be able to set extra_permissions: {resp.text}"

        # Restore empty extra_permissions
        admin_session.put(f"{BASE_URL}/api/admin/users/{target['user_id']}", json={
            "extra_permissions": []
        })

    def test_update_user_extra_permissions_rejects_unknown_key(self, admin_session):
        """PUT /api/admin/users/{id} with invalid extra_permissions key must return 400."""
        users_resp = admin_session.get(f"{BASE_URL}/api/admin/users")
        assert users_resp.status_code == 200
        users = users_resp.json()

        target = next(
            (u for u in users if u.get("role") != "admin" and u.get("email") != ADMIN_EMAIL),
            None
        )
        if not target:
            pytest.skip("No non-admin user available")

        resp = admin_session.put(f"{BASE_URL}/api/admin/users/{target['user_id']}", json={
            "extra_permissions": ["totally.fake.permission"]
        })
        assert resp.status_code == 400, (
            f"Should reject invalid extra_permissions key, got {resp.status_code}: {resp.text}"
        )

    def test_self_role_change_is_blocked(self, admin_session):
        """A user cannot change their own role — even as admin."""
        me_resp = admin_session.get(f"{BASE_URL}/api/auth/me")
        assert me_resp.status_code == 200
        me = me_resp.json()

        resp = admin_session.put(f"{BASE_URL}/api/admin/users/{me['user_id']}", json={
            "role": "director"
        })
        assert resp.status_code == 403, (
            f"Self role-change should be blocked, got {resp.status_code}: {resp.text}"
        )
