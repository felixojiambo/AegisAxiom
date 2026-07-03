"""
Test Sales Rep Dashboard feature and Commissions page View As removal
Tests:
1. /api/agent-dashboard/users-list?role=sales_rep returns only sales_rep users
2. /api/agent-dashboard endpoint with view_user_id parameter
3. Verify no authentication errors on these endpoints
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

@pytest.fixture(scope="module")
def auth_session():
    """Create authenticated session using login cookies"""
    session = requests.Session()
    login_res = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": "support@simplimeta.io",
        "password": ADMIN_PASSWORD
    })
    assert login_res.status_code == 200, f"Login failed: {login_res.status_code} - {login_res.text}"
    return session


class TestSalesRepDashboardAPI:
    """Tests for Sales Rep Dashboard API endpoints"""
    
    def test_users_list_sales_rep_filter(self, auth_session):
        """Test that /api/agent-dashboard/users-list?role=sales_rep returns only sales_rep users"""
        response = auth_session.get(f"{BASE_URL}/api/agent-dashboard/users-list?role=sales_rep")
        assert response.status_code == 200, f"API returned {response.status_code}: {response.text}"
        users = response.json()
        
        # Should return a list (may be empty if no sales_rep users exist)
        assert isinstance(users, list), f"Expected list, got {type(users)}"
        
        # If any users returned, they should all be sales_rep
        for user in users:
            assert user.get('role') == 'sales_rep', f"User {user.get('email')} has role {user.get('role')}, expected sales_rep"
        
        print(f"Found {len(users)} sales_rep users")
    
    def test_users_list_agent_filter(self, auth_session):
        """Test that /api/agent-dashboard/users-list?role=agent returns only agent users"""
        response = auth_session.get(f"{BASE_URL}/api/agent-dashboard/users-list?role=agent")
        assert response.status_code == 200, f"API returned {response.status_code}: {response.text}"
        users = response.json()
        
        # Should return a list (may be empty if no agent users exist)
        assert isinstance(users, list), f"Expected list, got {type(users)}"
        
        # If any users returned, they should all be agent
        for user in users:
            assert user.get('role') == 'agent', f"User {user.get('email')} has role {user.get('role')}, expected agent"
        
        print(f"Found {len(users)} agent users")
    
    def test_agent_dashboard_endpoint(self, auth_session):
        """Test that /api/agent-dashboard returns valid data"""
        response = auth_session.get(f"{BASE_URL}/api/agent-dashboard")
        assert response.status_code == 200, f"API returned {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify expected fields exist
        expected_keys = ['commissions', 'deals', 'summary', 'zoho_connected']
        for key in expected_keys:
            assert key in data, f"Missing expected key: {key}"
        
        print(f"Agent dashboard returned keys: {list(data.keys())}")
    
    def test_agent_dashboard_with_view_user_id(self, auth_session):
        """Test that /api/agent-dashboard accepts view_user_id parameter"""
        # Test with a fake user_id - should return 404 (User not found) which is correct behavior
        fake_user_id = "test_fake_user_id_12345"
        response = auth_session.get(f"{BASE_URL}/api/agent-dashboard?view_user_id={fake_user_id}")
        
        # Should return 404 for non-existent user
        assert response.status_code == 404, f"Expected 404 for non-existent user, got {response.status_code}"
        print(f"Correct: API returns 404 for non-existent user_id")
        
        # Test without view_user_id to get valid response
        response_valid = auth_session.get(f"{BASE_URL}/api/agent-dashboard")
        assert response_valid.status_code == 200
        data = response_valid.json()
        assert 'commissions' in data or 'summary' in data
        print(f"Dashboard without view_user_id returned keys: {list(data.keys())}")
    
    def test_accounts_list_endpoint(self, auth_session):
        """Test that /api/agent-dashboard/accounts-list returns valid data"""
        response = auth_session.get(f"{BASE_URL}/api/agent-dashboard/accounts-list")
        assert response.status_code == 200, f"API returned {response.status_code}: {response.text}"
        accounts = response.json()
        
        assert isinstance(accounts, list), f"Expected list, got {type(accounts)}"
        print(f"Found {len(accounts)} accounts")


class TestCommissionsAPI:
    """Tests for Commissions page API endpoints"""
    
    def test_commissions_endpoint(self, auth_session):
        """Test that /api/commissions returns valid data for admin"""
        response = auth_session.get(f"{BASE_URL}/api/commissions")
        assert response.status_code == 200, f"API returned {response.status_code}: {response.text}"
        data = response.json()
        
        # Can be a list or dict with items
        assert isinstance(data, (list, dict)), f"Expected list or dict, got {type(data)}"
        print(f"Commissions endpoint returned type: {type(data)}")
    
    def test_commissions_summary(self, auth_session):
        """Test that /api/commissions/summary returns valid data"""
        response = auth_session.get(f"{BASE_URL}/api/commissions/summary")
        assert response.status_code == 200, f"API returned {response.status_code}: {response.text}"
        data = response.json()
        print(f"Commissions summary: {data}")
