"""
AI Employee RBAC Tests
Verifies per-AI-employee access control: listing, access config read/write,
role validation, and user ID validation on the /access endpoint.
"""
import pytest
import requests
import os
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
def test_ai_employee(admin_session):
    """
    Create a minimal AI employee for use across tests.
    Cleans up after the session.
    """
    resp = admin_session.post(f"{BASE_URL}/api/ai-employees", json={
        "name": f"Test AI Employee {uuid.uuid4().hex[:6]}",
        "role": "Test Role",
        "type": "conversational",
        "description": "Created by automated RBAC test",
        "specialties": [],
        "allowed_roles": ["admin"],
        "allowed_user_ids": [],
    })
    if resp.status_code not in (200, 201):
        pytest.skip(f"Could not create AI employee for tests: {resp.text}")

    employee = resp.json()
    employee_id = employee.get("employee_id")

    yield employee_id

    # Cleanup
    admin_session.delete(f"{BASE_URL}/api/ai-employees/{employee_id}")


# ---------------------------------------------------------------------------
# List AI Employees
# ---------------------------------------------------------------------------

class TestAIEmployeeList:
    """GET /api/ai-employees returns employees and respects auth."""

    def test_admin_can_list_ai_employees(self, admin_session):
        """Admin with ai_employees.view should get 200 with a list."""
        resp = admin_session.get(f"{BASE_URL}/api/ai-employees")
        assert resp.status_code == 200, f"Admin should list AI employees: {resp.text}"
        assert isinstance(resp.json(), list), "Response should be a list"

    def test_unauthenticated_cannot_list_ai_employees(self):
        """Unauthenticated request must return 401."""
        resp = requests.get(f"{BASE_URL}/api/ai-employees")
        assert resp.status_code == 401, (
            f"Unauthenticated request should be blocked, got {resp.status_code}"
        )

    def test_list_includes_test_employee(self, admin_session, test_ai_employee):
        """The freshly created AI employee appears in the list for admin."""
        resp = admin_session.get(f"{BASE_URL}/api/ai-employees")
        assert resp.status_code == 200
        employees = resp.json()
        ids = [e.get("employee_id") for e in employees]
        assert test_ai_employee in ids, (
            f"Test AI employee {test_ai_employee} not found in admin's list"
        )

    def test_ai_employee_record_has_required_fields(self, admin_session, test_ai_employee):
        """Each AI employee record includes expected fields."""
        resp = admin_session.get(f"{BASE_URL}/api/ai-employees")
        assert resp.status_code == 200
        employees = resp.json()
        test_emp = next((e for e in employees if e.get("employee_id") == test_ai_employee), None)
        assert test_emp is not None, "Test employee not found in list"

        for field in ("employee_id", "name", "type", "status", "allowed_roles", "allowed_user_ids"):
            assert field in test_emp, f"AI employee record missing field: {field}"


# ---------------------------------------------------------------------------
# Access Config — Read
# ---------------------------------------------------------------------------

class TestAIEmployeeAccessRead:
    """GET /api/ai-employees/{id}/access requires ai_employees.manage."""

    def test_admin_can_read_access_config(self, admin_session, test_ai_employee):
        """Admin can read access configuration."""
        resp = admin_session.get(f"{BASE_URL}/api/ai-employees/{test_ai_employee}/access")
        assert resp.status_code == 200, f"Admin should read access config: {resp.text}"
        data = resp.json()
        assert "allowed_roles" in data
        assert "allowed_user_ids" in data
        assert data["employee_id"] == test_ai_employee

    def test_unauthenticated_cannot_read_access_config(self, test_ai_employee):
        """Unauthenticated request to access endpoint returns 401."""
        resp = requests.get(f"{BASE_URL}/api/ai-employees/{test_ai_employee}/access")
        assert resp.status_code == 401, (
            f"Unauthenticated access should be blocked, got {resp.status_code}"
        )

    def test_access_config_for_nonexistent_employee_returns_404(self, admin_session):
        """GET /api/ai-employees/bogus_id/access returns 404."""
        resp = admin_session.get(f"{BASE_URL}/api/ai-employees/ai_doesnotexist_0000/access")
        assert resp.status_code == 404, (
            f"Nonexistent employee should return 404, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Access Config — Write
# ---------------------------------------------------------------------------

class TestAIEmployeeAccessUpdate:
    """PUT /api/ai-employees/{id}/access updates allowed_roles and allowed_user_ids."""

    def test_admin_can_update_allowed_roles(self, admin_session, test_ai_employee):
        """PUT /access with valid roles updates the employee's allowed_roles."""
        resp = admin_session.put(f"{BASE_URL}/api/ai-employees/{test_ai_employee}/access", json={
            "allowed_roles": ["admin", "director"],
        })
        assert resp.status_code == 200, f"Should update allowed_roles: {resp.text}"

        # Verify the change was persisted
        read_resp = admin_session.get(f"{BASE_URL}/api/ai-employees/{test_ai_employee}/access")
        assert read_resp.status_code == 200
        data = read_resp.json()
        assert "director" in data["allowed_roles"], (
            f"allowed_roles should include 'director', got: {data['allowed_roles']}"
        )

    def test_update_allowed_roles_rejects_invalid_role_name(self, admin_session, test_ai_employee):
        """PUT /access with a non-existent role name must return 400."""
        resp = admin_session.put(f"{BASE_URL}/api/ai-employees/{test_ai_employee}/access", json={
            "allowed_roles": ["admin", "totally_fake_role_xyz"],
        })
        assert resp.status_code == 400, (
            f"Should reject invalid role name, got {resp.status_code}: {resp.text}"
        )

    def test_update_allowed_user_ids_rejects_invalid_user_id(self, admin_session, test_ai_employee):
        """PUT /access with a non-existent user ID must return 400."""
        resp = admin_session.put(f"{BASE_URL}/api/ai-employees/{test_ai_employee}/access", json={
            "allowed_user_ids": ["user_doesnotexist_0000"],
        })
        assert resp.status_code == 400, (
            f"Should reject invalid user ID, got {resp.status_code}: {resp.text}"
        )

    def test_update_allowed_user_ids_with_real_admin_user(self, admin_session, test_ai_employee):
        """PUT /access with a real admin user ID succeeds."""
        # Fetch admin's user_id
        me_resp = admin_session.get(f"{BASE_URL}/api/auth/me")
        assert me_resp.status_code == 200
        admin_user_id = me_resp.json().get("user_id")

        resp = admin_session.put(f"{BASE_URL}/api/ai-employees/{test_ai_employee}/access", json={
            "allowed_user_ids": [admin_user_id],
        })
        assert resp.status_code == 200, (
            f"Should accept a valid user ID, got {resp.status_code}: {resp.text}"
        )

        # Verify persisted
        read_resp = admin_session.get(f"{BASE_URL}/api/ai-employees/{test_ai_employee}/access")
        assert read_resp.status_code == 200
        data = read_resp.json()
        assert admin_user_id in data["allowed_user_ids"], (
            f"Admin user_id should be in allowed_user_ids, got: {data['allowed_user_ids']}"
        )

    def test_unauthenticated_cannot_update_access(self, test_ai_employee):
        """Unauthenticated PUT to /access returns 401."""
        resp = requests.put(f"{BASE_URL}/api/ai-employees/{test_ai_employee}/access", json={
            "allowed_roles": ["admin"],
        })
        assert resp.status_code == 401, (
            f"Unauthenticated update should be blocked, got {resp.status_code}"
        )

    def test_update_access_on_nonexistent_employee_returns_404(self, admin_session):
        """PUT /access for a bogus employee ID returns 404."""
        resp = admin_session.put(f"{BASE_URL}/api/ai-employees/ai_doesnotexist_0000/access", json={
            "allowed_roles": ["admin"],
        })
        assert resp.status_code == 404, (
            f"Nonexistent employee should return 404, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# API Key Masking
# ---------------------------------------------------------------------------

class TestAIEmployeeApiKeyMasking:
    """N8N API keys must not be returned in full from list or detail endpoints."""

    @pytest.fixture
    def n8n_employee(self, admin_session):
        """Create an N8N AI employee with a mock API key."""
        resp = admin_session.post(f"{BASE_URL}/api/ai-employees", json={
            "name": f"Test N8N Employee {uuid.uuid4().hex[:6]}",
            "role": "Automation",
            "type": "n8n",
            "description": "Created by automated RBAC test",
            "n8n_instance_url": "https://n8n.example.com",
            "n8n_api_key": "super_secret_api_key_12345",
            "n8n_workflow_id": "workflow_abc123",
            "allowed_roles": ["admin"],
            "allowed_user_ids": [],
        })
        if resp.status_code not in (200, 201):
            pytest.skip(f"Could not create N8N test employee: {resp.text}")

        employee = resp.json()
        employee_id = employee.get("employee_id")
        yield employee_id

        admin_session.delete(f"{BASE_URL}/api/ai-employees/{employee_id}")

    def test_n8n_api_key_is_masked_in_list(self, admin_session, n8n_employee):
        """N8N API key must be masked (not returned in full) in the list endpoint."""
        resp = admin_session.get(f"{BASE_URL}/api/ai-employees")
        assert resp.status_code == 200
        employees = resp.json()
        emp = next((e for e in employees if e.get("employee_id") == n8n_employee), None)
        assert emp is not None, "N8N employee should appear in admin list"

        conn = emp.get("connection", {})
        api_key_val = conn.get("n8n_api_key", "")
        assert "super_secret_api_key_12345" not in api_key_val, (
            "N8N API key should be masked in list response — full key must not be returned"
        )
