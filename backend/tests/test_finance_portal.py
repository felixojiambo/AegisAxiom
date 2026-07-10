"""
Finance Portal API Tests
Tests for the new Finance Portal feature:
- /api/finance/overview - KPI data
- /api/finance/commissions - Paginated/filterable commissions
- /api/finance/zoho-commissions - Paginated Zoho commissions
- /api/finance/users-summary - Sales users list
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
ADMIN_EMAIL = "support@simplimeta.io"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


class TestFinancePortalAuth:
    """Test authentication and setup"""
    
    @pytest.fixture(scope="class")
    def session(self):
        """Create requests session"""
        return requests.Session()
    
    @pytest.fixture(scope="class")
    def auth_token(self, session):
        """Login and get auth token"""
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "session_token" in data, "No session token in response"
        return data["session_token"]
    
    @pytest.fixture(scope="class")
    def auth_headers(self, auth_token):
        """Get auth headers"""
        return {"Authorization": f"Bearer {auth_token}"}
    
    def test_login_success(self, session, auth_token):
        """Verify login works and returns a token"""
        assert auth_token is not None
        assert len(auth_token) > 0
        print(f"✓ Login successful, token length: {len(auth_token)}")


class TestFinanceOverviewAPI:
    """Test /api/finance/overview endpoint"""
    
    @pytest.fixture(scope="class")
    def session(self):
        return requests.Session()
    
    @pytest.fixture(scope="class")
    def auth_headers(self, session):
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        token = response.json().get("session_token")
        return {"Authorization": f"Bearer {token}"}
    
    def test_overview_returns_200(self, session, auth_headers):
        """Test overview endpoint returns 200"""
        response = session.get(f"{BASE_URL}/api/finance/overview", headers=auth_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ GET /api/finance/overview returns 200")
    
    def test_overview_response_structure(self, session, auth_headers):
        """Test overview response has correct structure"""
        response = session.get(f"{BASE_URL}/api/finance/overview", headers=auth_headers)
        data = response.json()
        
        # Check top-level keys
        assert "commissions" in data, "Missing 'commissions' in response"
        assert "zoho_commissions" in data, "Missing 'zoho_commissions' in response"
        assert "invoices" in data, "Missing 'invoices' in response"
        
        # Check commissions structure
        comms = data["commissions"]
        assert "total_owed" in comms, "Missing 'total_owed' in commissions"
        assert "total_paid" in comms, "Missing 'total_paid' in commissions"
        assert "total_potential" in comms, "Missing 'total_potential' in commissions"
        assert "count" in comms, "Missing 'count' in commissions"
        assert "by_status" in comms, "Missing 'by_status' in commissions"
        assert "by_user" in comms, "Missing 'by_user' in commissions"
        
        # Check zoho_commissions structure
        zoho = data["zoho_commissions"]
        assert "agent_total" in zoho
        assert "rep_total" in zoho
        assert "dir_total" in zoho
        assert "count" in zoho
        assert "by_rep" in zoho
        
        # Check invoices structure
        inv = data["invoices"]
        assert "total" in inv
        assert "paid" in inv
        assert "outstanding" in inv
        assert "count" in inv
        
        print("✓ Overview response has correct structure (commissions, zoho_commissions, invoices)")
    
    def test_overview_numeric_values(self, session, auth_headers):
        """Test overview returns numeric values"""
        response = session.get(f"{BASE_URL}/api/finance/overview", headers=auth_headers)
        data = response.json()
        
        # Verify numeric types
        assert isinstance(data["commissions"]["total_owed"], (int, float))
        assert isinstance(data["commissions"]["total_paid"], (int, float))
        assert isinstance(data["commissions"]["count"], int)
        assert isinstance(data["zoho_commissions"]["count"], int)
        assert isinstance(data["invoices"]["total"], (int, float))
        
        print(f"✓ Overview numeric values: owed=${data['commissions']['total_owed']}, count={data['commissions']['count']}")
    
    def test_overview_requires_auth(self, session):
        """Test overview requires authentication"""
        response = session.get(f"{BASE_URL}/api/finance/overview")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print("✓ Overview endpoint requires authentication")


class TestFinanceCommissionsAPI:
    """Test /api/finance/commissions endpoint"""
    
    @pytest.fixture(scope="class")
    def session(self):
        return requests.Session()
    
    @pytest.fixture(scope="class")
    def auth_headers(self, session):
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        token = response.json().get("session_token")
        return {"Authorization": f"Bearer {token}"}
    
    def test_commissions_returns_200(self, session, auth_headers):
        """Test commissions endpoint returns 200"""
        response = session.get(f"{BASE_URL}/api/finance/commissions", headers=auth_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ GET /api/finance/commissions returns 200")
    
    def test_commissions_pagination_structure(self, session, auth_headers):
        """Test commissions returns paginated structure"""
        response = session.get(f"{BASE_URL}/api/finance/commissions", headers=auth_headers)
        data = response.json()
        
        # Check pagination fields
        assert "items" in data, "Missing 'items' in response"
        assert "total" in data, "Missing 'total' in response"
        assert "skip" in data, "Missing 'skip' in response"
        assert "limit" in data, "Missing 'limit' in response"
        
        assert isinstance(data["items"], list)
        assert isinstance(data["total"], int)
        
        print(f"✓ Commissions returns paginated structure: {data['total']} total items")
    
    def test_commissions_skip_limit(self, session, auth_headers):
        """Test commissions pagination params work"""
        response = session.get(f"{BASE_URL}/api/finance/commissions?skip=0&limit=10", headers=auth_headers)
        data = response.json()
        
        assert data["skip"] == 0
        assert data["limit"] == 10
        assert len(data["items"]) <= 10
        
        print(f"✓ Commissions pagination works: skip=0, limit=10, returned {len(data['items'])} items")
    
    def test_commissions_status_filter(self, session, auth_headers):
        """Test commissions status filter"""
        response = session.get(f"{BASE_URL}/api/finance/commissions?status=pending", headers=auth_headers)
        data = response.json()
        
        assert response.status_code == 200
        # All items should have status=pending if any exist
        for item in data["items"]:
            assert item.get("status") == "pending", f"Expected status='pending', got '{item.get('status')}'"
        
        print(f"✓ Commissions status filter works: {len(data['items'])} pending items")
    
    def test_commissions_requires_auth(self, session):
        """Test commissions requires authentication"""
        response = session.get(f"{BASE_URL}/api/finance/commissions")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print("✓ Commissions endpoint requires authentication")


class TestFinanceZohoCommissionsAPI:
    """Test /api/finance/zoho-commissions endpoint"""
    
    @pytest.fixture(scope="class")
    def session(self):
        return requests.Session()
    
    @pytest.fixture(scope="class")
    def auth_headers(self, session):
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        token = response.json().get("session_token")
        return {"Authorization": f"Bearer {token}"}
    
    def test_zoho_commissions_returns_200(self, session, auth_headers):
        """Test Zoho commissions endpoint returns 200"""
        response = session.get(f"{BASE_URL}/api/finance/zoho-commissions", headers=auth_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ GET /api/finance/zoho-commissions returns 200")
    
    def test_zoho_commissions_pagination_structure(self, session, auth_headers):
        """Test Zoho commissions returns paginated structure"""
        response = session.get(f"{BASE_URL}/api/finance/zoho-commissions", headers=auth_headers)
        data = response.json()
        
        assert "items" in data
        assert "total" in data
        assert "skip" in data
        assert "limit" in data
        
        print(f"✓ Zoho commissions returns paginated structure: {data['total']} total items")
    
    def test_zoho_commissions_requires_auth(self, session):
        """Test Zoho commissions requires authentication"""
        response = session.get(f"{BASE_URL}/api/finance/zoho-commissions")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print("✓ Zoho commissions endpoint requires authentication")


class TestFinanceUsersSummaryAPI:
    """Test /api/finance/users-summary endpoint"""
    
    @pytest.fixture(scope="class")
    def session(self):
        return requests.Session()
    
    @pytest.fixture(scope="class")
    def auth_headers(self, session):
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        token = response.json().get("session_token")
        return {"Authorization": f"Bearer {token}"}
    
    def test_users_summary_returns_200(self, session, auth_headers):
        """Test users-summary endpoint returns 200"""
        response = session.get(f"{BASE_URL}/api/finance/users-summary", headers=auth_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("✓ GET /api/finance/users-summary returns 200")
    
    def test_users_summary_returns_list(self, session, auth_headers):
        """Test users-summary returns a list"""
        response = session.get(f"{BASE_URL}/api/finance/users-summary", headers=auth_headers)
        data = response.json()
        
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        print(f"✓ Users-summary returns list with {len(data)} users")
    
    def test_users_summary_requires_auth(self, session):
        """Test users-summary requires authentication"""
        response = session.get(f"{BASE_URL}/api/finance/users-summary")
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print("✓ Users-summary endpoint requires authentication")


class TestFinanceRoleInDatabase:
    """Test finance role exists in roles collection"""
    
    @pytest.fixture(scope="class")
    def session(self):
        return requests.Session()
    
    @pytest.fixture(scope="class")
    def auth_headers(self, session):
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        token = response.json().get("session_token")
        return {"Authorization": f"Bearer {token}"}
    
    def test_finance_role_exists(self, session, auth_headers):
        """Test finance role is seeded in roles collection"""
        response = session.get(f"{BASE_URL}/api/roles", headers=auth_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        roles = response.json()
        finance_role = next((r for r in roles if r.get("name") == "finance"), None)
        
        assert finance_role is not None, "Finance role not found in roles collection"
        assert finance_role.get("display_name") == "Finance"
        assert "finance.portal" in finance_role.get("permissions", [])
        assert "finance.analytics" in finance_role.get("permissions", [])
        assert "finance.export" in finance_role.get("permissions", [])
        assert "commissions.view.all" in finance_role.get("permissions", [])
        
        print(f"✓ Finance role exists with {len(finance_role.get('permissions', []))} permissions")


class TestFinanceRoleInModels:
    """Test finance role is in valid roles list"""
    
    @pytest.fixture(scope="class")
    def session(self):
        return requests.Session()
    
    @pytest.fixture(scope="class")
    def auth_headers(self, session):
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        token = response.json().get("session_token")
        return {"Authorization": f"Bearer {token}"}
    
    def test_finance_role_in_invite_validation(self, session, auth_headers):
        """Test finance role is accepted in user invite"""
        # Try inviting with finance role - should not fail with 'invalid role'
        response = session.post(f"{BASE_URL}/api/admin/invite", headers=auth_headers, json={
            "email": "test_finance_role_check@example.com",
            "name": "Test Finance",
            "role": "finance"
        })
        
        # Should be 200 (success) or 400 (email exists) but NOT role validation error
        if response.status_code == 400 and "Invalid role" in response.text:
            pytest.fail("Finance role not in VALID_ROLES list")
        
        # If 200, delete the test user
        if response.status_code == 200:
            data = response.json()
            user_id = data.get("user_id")
            if user_id:
                session.delete(f"{BASE_URL}/api/admin/users/{user_id}", headers=auth_headers)
        
        print("✓ Finance role is accepted in user invite validation")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
