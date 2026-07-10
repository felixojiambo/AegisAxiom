"""
Test: View As Dropdown on Commissions Page (Backend APIs)
Features tested:
- GET /api/agent-dashboard/users-list?role=sales_rep returns only sales_rep users
- GET /api/agent-dashboard/users-list (no role filter) returns both agents AND sales_reps
- GET /api/agent-dashboard?view_user_id=xxx returns filtered commission data
- POST /api/admin/invite creates sales_rep users
"""

import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

class TestViewAsCommissionsBackend:
    """Backend tests for View As feature on Commissions page"""
    
    @pytest.fixture(scope="class")
    def admin_session(self):
        """Admin session for all tests"""
        session = requests.Session()
        login_res = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": ADMIN_PASSWORD
        })
        assert login_res.status_code == 200, f"Admin login failed: {login_res.text}"
        return session
    
    @pytest.fixture(scope="class")
    def test_sales_rep(self, admin_session):
        """Create a test sales_rep user and cleanup after tests"""
        unique_id = uuid.uuid4().hex[:8]
        email = f"TEST_salesrep_{unique_id}@test.com"
        name = f"TEST Sales Rep {unique_id}"
        
        # Create sales_rep via invite
        invite_res = admin_session.post(f"{BASE_URL}/api/admin/invite", json={
            "email": email,
            "name": name,
            "role": "sales_rep"
        })
        assert invite_res.status_code == 200, f"Failed to create sales_rep: {invite_res.text}"
        data = invite_res.json()
        user_id = data.get("user_id")
        
        # Activate the user so they appear in users-list (is_active: True filter)
        activate_res = admin_session.put(f"{BASE_URL}/api/admin/users/{user_id}", json={
            "is_active": True
        })
        assert activate_res.status_code == 200, f"Failed to activate sales_rep: {activate_res.text}"
        
        yield {"user_id": user_id, "email": email, "name": name}
        
        # Cleanup: Delete the test user
        delete_res = admin_session.delete(f"{BASE_URL}/api/admin/users/{user_id}")
        print(f"Cleanup: Deleted test sales_rep {user_id}, status: {delete_res.status_code}")
    
    @pytest.fixture(scope="class")
    def test_agent(self, admin_session):
        """Create a test agent user and cleanup after tests"""
        unique_id = uuid.uuid4().hex[:8]
        email = f"TEST_agent_{unique_id}@test.com"
        name = f"TEST Agent {unique_id}"
        
        # Create agent via invite
        invite_res = admin_session.post(f"{BASE_URL}/api/admin/invite", json={
            "email": email,
            "name": name,
            "role": "agent"
        })
        assert invite_res.status_code == 200, f"Failed to create agent: {invite_res.text}"
        data = invite_res.json()
        user_id = data.get("user_id")
        
        # Activate the user so they appear in users-list (is_active: True filter)
        activate_res = admin_session.put(f"{BASE_URL}/api/admin/users/{user_id}", json={
            "is_active": True
        })
        assert activate_res.status_code == 200, f"Failed to activate agent: {activate_res.text}"
        
        yield {"user_id": user_id, "email": email, "name": name}
        
        # Cleanup: Delete the test user
        delete_res = admin_session.delete(f"{BASE_URL}/api/admin/users/{user_id}")
        print(f"Cleanup: Deleted test agent {user_id}, status: {delete_res.status_code}")
    
    # ---- TEST: users-list endpoint ----
    
    def test_users_list_with_role_sales_rep(self, admin_session, test_sales_rep):
        """GET /api/agent-dashboard/users-list?role=sales_rep returns only sales_rep users"""
        res = admin_session.get(f"{BASE_URL}/api/agent-dashboard/users-list?role=sales_rep")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        users = res.json()
        assert isinstance(users, list), "Response should be a list"
        
        # Verify all returned users are sales_reps
        for u in users:
            assert u.get("role") == "sales_rep", f"Expected sales_rep role, got {u.get('role')}"
        
        # Verify our test sales_rep is in the list
        user_ids = [u.get("user_id") for u in users]
        assert test_sales_rep["user_id"] in user_ids, "Test sales_rep should be in the list"
        print(f"Found {len(users)} sales_rep users")
    
    def test_users_list_without_role_filter(self, admin_session, test_sales_rep, test_agent):
        """GET /api/agent-dashboard/users-list (no role) returns both agents AND sales_reps"""
        res = admin_session.get(f"{BASE_URL}/api/agent-dashboard/users-list")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        users = res.json()
        assert isinstance(users, list), "Response should be a list"
        
        # Verify only agents and sales_reps are returned
        roles = set(u.get("role") for u in users)
        for role in roles:
            assert role in ["agent", "sales_rep"], f"Unexpected role: {role}"
        
        # Verify both test users are in the list
        user_ids = [u.get("user_id") for u in users]
        assert test_sales_rep["user_id"] in user_ids, "Test sales_rep should be in the list"
        assert test_agent["user_id"] in user_ids, "Test agent should be in the list"
        print(f"Found {len(users)} users (agents + sales_reps). Roles: {roles}")
    
    def test_users_list_structure(self, admin_session, test_sales_rep):
        """Verify users-list returns correct data structure"""
        res = admin_session.get(f"{BASE_URL}/api/agent-dashboard/users-list?role=sales_rep")
        assert res.status_code == 200
        users = res.json()
        
        if len(users) > 0:
            user = users[0]
            # Verify expected fields
            assert "user_id" in user, "user_id field missing"
            assert "name" in user, "name field missing"
            assert "email" in user, "email field missing"
            assert "role" in user, "role field missing"
            # Should NOT contain sensitive data
            assert "password_hash" not in user, "password_hash should not be returned"
    
    # ---- TEST: agent-dashboard view_user_id ----
    
    def test_view_user_id_returns_commission_data(self, admin_session, test_sales_rep):
        """GET /api/agent-dashboard?view_user_id=xxx returns commission data for user"""
        res = admin_session.get(f"{BASE_URL}/api/agent-dashboard?view_user_id={test_sales_rep['user_id']}")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        
        # Verify response structure
        assert "summary" in data, "summary field missing"
        assert "commissions" in data, "commissions field missing"
        assert "target_role" in data, "target_role field missing"
        assert "target_email" in data, "target_email field missing"
        assert "view_label" in data, "view_label field missing"
        
        # Verify target_role matches the user's role
        assert data["target_role"] == "sales_rep", f"Expected sales_rep, got {data['target_role']}"
        
        # Verify target_email matches
        assert data["target_email"] == test_sales_rep["email"].lower(), "target_email mismatch"
        
        # Verify view_label contains user info
        assert test_sales_rep["name"] in data["view_label"], "view_label should contain user name"
        
        # Summary should have valid structure
        summary = data["summary"]
        assert "owed_count" in summary, "owed_count missing"
        assert "paid_count" in summary, "paid_count missing"
        assert "potential_count" in summary, "potential_count missing"
        assert "commission_owed" in summary, "commission_owed missing"
        assert "commission_paid" in summary, "commission_paid missing"
        assert "commission_potential" in summary, "commission_potential missing"
        
        print(f"View As data for {test_sales_rep['name']}: summary={summary}")
    
    def test_view_user_id_invalid_user(self, admin_session):
        """GET /api/agent-dashboard?view_user_id=invalid returns 404"""
        res = admin_session.get(f"{BASE_URL}/api/agent-dashboard?view_user_id=invalid_user_id")
        assert res.status_code == 404, f"Expected 404, got {res.status_code}: {res.text}"
    
    # ---- TEST: admin-only access ----
    
    def test_users_list_requires_admin(self):
        """users-list endpoint requires admin/director role"""
        session = requests.Session()
        # Try without auth
        res = session.get(f"{BASE_URL}/api/agent-dashboard/users-list")
        assert res.status_code == 401, f"Expected 401 for unauthenticated request, got {res.status_code}"
    
    def test_view_user_id_requires_admin(self, test_sales_rep):
        """view_user_id parameter requires admin/director role"""
        session = requests.Session()
        # Try without auth
        res = session.get(f"{BASE_URL}/api/agent-dashboard?view_user_id={test_sales_rep['user_id']}")
        assert res.status_code == 401, f"Expected 401 for unauthenticated request, got {res.status_code}"
    
    # ---- TEST: agent-dashboard base endpoint ----
    
    def test_agent_dashboard_base(self, admin_session):
        """GET /api/agent-dashboard returns admin's own dashboard data"""
        res = admin_session.get(f"{BASE_URL}/api/agent-dashboard")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        
        # Admin viewing their own dashboard
        assert data.get("target_role") == "admin", f"Expected admin target_role, got {data.get('target_role')}"
        assert data.get("view_label") is None, "view_label should be None for own dashboard"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
