import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Admin credentials from env
ADMIN_EMAIL = "support@simplimeta.io"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


class TestAuditLogAPI:
    """Test Audit Log API endpoints"""
    
    @pytest.fixture(scope="class")
    def admin_session(self):
        """Get authenticated admin session"""
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        
        # Login as admin
        login_resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Admin login failed: {login_resp.text}"
        return session
    
    def test_get_audit_logs_returns_paginated_response(self, admin_session):
        """Test GET /api/audit returns {items, total, skip, limit}"""
        resp = admin_session.get(f"{BASE_URL}/api/audit")
        assert resp.status_code == 200, f"Failed: {resp.text}"
        
        data = resp.json()
        assert "items" in data, "Response missing 'items' field"
        assert "total" in data, "Response missing 'total' field"
        assert "skip" in data, "Response missing 'skip' field"
        assert "limit" in data, "Response missing 'limit' field"
        assert isinstance(data["items"], list), "items should be a list"
        assert isinstance(data["total"], int), "total should be an integer"
        print(f"Audit logs returned: {len(data['items'])} items, {data['total']} total")
    
    def test_get_audit_logs_with_pagination(self, admin_session):
        """Test GET /api/audit with skip and limit params"""
        resp = admin_session.get(f"{BASE_URL}/api/audit?skip=0&limit=5")
        assert resp.status_code == 200
        
        data = resp.json()
        assert len(data["items"]) <= 5, "Limit not respected"
        assert data["skip"] == 0, "Skip value incorrect"
        assert data["limit"] == 5, "Limit value incorrect"
        print(f"Pagination test passed: {len(data['items'])} items returned")
    
    def test_filter_by_action(self, admin_session):
        """Test GET /api/audit?action=login filters correctly"""
        resp = admin_session.get(f"{BASE_URL}/api/audit?action=login")
        assert resp.status_code == 200
        
        data = resp.json()
        for item in data["items"]:
            assert item["action"] == "login", f"Filter failed: got action={item['action']}"
        print(f"Action filter test passed: {len(data['items'])} login events")
    
    def test_filter_by_resource_type(self, admin_session):
        """Test GET /api/audit?resource_type=auth filters correctly"""
        resp = admin_session.get(f"{BASE_URL}/api/audit?resource_type=auth")
        assert resp.status_code == 200
        
        data = resp.json()
        for item in data["items"]:
            assert item["resource_type"] == "auth", f"Filter failed: got resource_type={item['resource_type']}"
        print(f"Resource type filter test passed: {len(data['items'])} auth events")
    
    def test_filter_by_actor_id(self, admin_session):
        """Test GET /api/audit?actor_id=<id> filters correctly"""
        # First get all logs to find an actor_id
        all_resp = admin_session.get(f"{BASE_URL}/api/audit?limit=1")
        assert all_resp.status_code == 200
        
        data = all_resp.json()
        if data["items"]:
            actor_id = data["items"][0]["actor_id"]
            
            # Now filter by that actor_id
            resp = admin_session.get(f"{BASE_URL}/api/audit?actor_id={actor_id}")
            assert resp.status_code == 200
            
            filtered_data = resp.json()
            for item in filtered_data["items"]:
                assert item["actor_id"] == actor_id, f"Actor filter failed"
            print(f"Actor ID filter test passed: {len(filtered_data['items'])} events for actor {actor_id}")
        else:
            print("No audit logs to test actor_id filter")
    
    def test_combined_filters(self, admin_session):
        """Test multiple filters together"""
        resp = admin_session.get(f"{BASE_URL}/api/audit?action=login&resource_type=auth")
        assert resp.status_code == 200
        
        data = resp.json()
        for item in data["items"]:
            assert item["action"] == "login"
            assert item["resource_type"] == "auth"
        print(f"Combined filters test passed: {len(data['items'])} matching events")
    
    def test_audit_log_entry_structure(self, admin_session):
        """Test audit log entry has required fields"""
        resp = admin_session.get(f"{BASE_URL}/api/audit?limit=1")
        assert resp.status_code == 200
        
        data = resp.json()
        if data["items"]:
            item = data["items"][0]
            required_fields = ["timestamp", "action", "actor_id", "actor_email", "resource_type"]
            for field in required_fields:
                assert field in item, f"Missing required field: {field}"
            print(f"Entry structure verified. Fields: {list(item.keys())}")
        else:
            print("No audit log entries to verify structure")
    
    def test_unauthorized_access_denied(self):
        """Test that unauthenticated requests are denied"""
        session = requests.Session()
        resp = session.get(f"{BASE_URL}/api/audit")
        assert resp.status_code in [401, 403], f"Expected 401/403 but got {resp.status_code}"
        print("Unauthorized access correctly denied")


class TestExistingEndpoints:
    """Verify existing endpoints still work after audit changes"""
    
    @pytest.fixture(scope="class")
    def admin_session(self):
        """Get authenticated admin session"""
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        
        login_resp = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Admin login failed: {login_resp.text}"
        return session
    
    def test_accounts_endpoint(self, admin_session):
        """Verify /api/accounts still works"""
        resp = admin_session.get(f"{BASE_URL}/api/accounts?skip=0&limit=5")
        assert resp.status_code == 200, f"Accounts failed: {resp.text}"
        data = resp.json()
        assert "items" in data and "total" in data
        print(f"Accounts endpoint working: {data['total']} total")
    
    def test_deals_endpoint(self, admin_session):
        """Verify /api/deals still works"""
        resp = admin_session.get(f"{BASE_URL}/api/deals?skip=0&limit=5")
        assert resp.status_code == 200, f"Deals failed: {resp.text}"
        data = resp.json()
        assert "items" in data and "total" in data
        print(f"Deals endpoint working: {data['total']} total")
    
    def test_dashboard_endpoint(self, admin_session):
        """Verify /api/dashboard still works"""
        resp = admin_session.get(f"{BASE_URL}/api/dashboard")
        assert resp.status_code == 200, f"Dashboard failed: {resp.text}"
        print("Dashboard endpoint working")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
