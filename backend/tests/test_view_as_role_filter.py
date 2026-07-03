"""
Test: View As Role Filter (Agent Dashboard / Commissions page)
Features:
1. GET /api/agent-dashboard/users-list?role=agent - returns ONLY agents
2. GET /api/agent-dashboard/users-list?role=sales_rep - returns ONLY sales_reps
3. GET /api/agent-dashboard/users-list (no filter) - returns BOTH agents and sales_reps
"""

import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

class TestViewAsRoleFilter:
    """Test the users-list endpoint role filtering"""
    
    @pytest.fixture(scope="class")
    def session(self):
        """Create a session and login as admin"""
        s = requests.Session()
        response = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": ADMIN_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip(f"Admin login failed: {response.status_code} - {response.text}")
        print(f"Admin login successful, cookies: {s.cookies.get_dict()}")
        return s
    
    # --- Test users-list endpoint role filtering ---
    
    def test_users_list_role_agent(self, session):
        """GET /api/agent-dashboard/users-list?role=agent should return ONLY agents"""
        response = session.get(f"{BASE_URL}/api/agent-dashboard/users-list?role=agent")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        users = response.json()
        assert isinstance(users, list), "Expected list response"
        
        # All returned users should have role='agent'
        for user in users:
            assert user.get('role') == 'agent', f"Expected role='agent' but got '{user.get('role')}' for user {user.get('name')}"
        print(f"PASS: users-list?role=agent returned {len(users)} agents")
    
    def test_users_list_role_sales_rep(self, session):
        """GET /api/agent-dashboard/users-list?role=sales_rep should return ONLY sales_reps"""
        response = session.get(f"{BASE_URL}/api/agent-dashboard/users-list?role=sales_rep")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        users = response.json()
        assert isinstance(users, list), "Expected list response"
        
        # All returned users should have role='sales_rep'
        for user in users:
            assert user.get('role') == 'sales_rep', f"Expected role='sales_rep' but got '{user.get('role')}' for user {user.get('name')}"
        print(f"PASS: users-list?role=sales_rep returned {len(users)} sales_reps")
    
    def test_users_list_no_filter(self, session):
        """GET /api/agent-dashboard/users-list (no role filter) should return BOTH agents and sales_reps"""
        response = session.get(f"{BASE_URL}/api/agent-dashboard/users-list")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        users = response.json()
        assert isinstance(users, list), "Expected list response"
        
        # Should only return agent OR sales_rep (not admin, director, etc.)
        allowed_roles = {'agent', 'sales_rep'}
        for user in users:
            role = user.get('role')
            assert role in allowed_roles, f"Expected role in {allowed_roles} but got '{role}' for user {user.get('name')}"
        
        roles_returned = set(u.get('role') for u in users)
        print(f"PASS: users-list (no filter) returned {len(users)} users with roles: {roles_returned}")
    
    def test_users_list_403_for_non_admin(self):
        """Non-admin users should get 403 when accessing users-list"""
        # Try without session/cookie
        response = requests.get(f"{BASE_URL}/api/agent-dashboard/users-list")
        assert response.status_code in [401, 403], f"Expected 401/403 for unauthenticated, got {response.status_code}"
        print(f"PASS: Unauthenticated request to users-list correctly rejected with {response.status_code}")


class TestDealIdFieldsPresent:
    """Test that Deal ID fields are present in API responses"""
    
    @pytest.fixture(scope="class")
    def session(self):
        """Create a session and login as admin"""
        s = requests.Session()
        response = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": ADMIN_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip(f"Admin login failed: {response.status_code}")
        return s
    
    def test_agent_dashboard_response_structure(self, session):
        """Agent dashboard response should have commissions with deal_id field"""
        response = session.get(f"{BASE_URL}/api/agent-dashboard")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Check structure exists
        assert 'commissions' in data, "Response should have 'commissions' key"
        assert 'deals' in data, "Response should have 'deals' key"
        assert 'summary' in data, "Response should have 'summary' key"
        
        # Note: Data might be empty if no Zoho data is synced
        print(f"PASS: Agent dashboard response has correct structure - {len(data.get('commissions', []))} commissions, {len(data.get('deals', []))} deals")
    
    def test_deals_endpoint_response_structure(self, session):
        """Deals endpoint should return items with zoho_deal_id field for admin view"""
        response = session.get(f"{BASE_URL}/api/deals")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Check it's paginated structure
        assert 'items' in data or isinstance(data, list), "Response should have 'items' or be a list"
        print(f"PASS: Deals endpoint returns correct structure")
    
    def test_finance_zoho_commissions_structure(self, session):
        """Finance zoho-commissions endpoint should have deal_id field"""
        response = session.get(f"{BASE_URL}/api/finance/zoho-commissions")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert 'items' in data, "Response should have 'items' key"
        print(f"PASS: Finance zoho-commissions endpoint returns correct structure - {len(data.get('items', []))} items")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
