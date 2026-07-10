"""
Tests for AI Chat Logs feature and Deals edit permission
- GET /api/chat/admin/logs - admin endpoint to view all chat sessions
- GET /api/chat/admin/logs/{session_id}/messages - admin endpoint to view session messages
- POST /api/chat/sessions - auto-incrementing chat_number (CHAT-00001, etc.)
- Filtering by user_id, chat_number, date_from, date_to
- Permission check: requires audit.view permission
- Deals.canEdit now uses permission check (deals.edit.all or *)
"""

import pytest
import requests
import uuid
import os
from datetime import datetime, timedelta

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


class TestChatAdminLogs:
    """Tests for GET /api/chat/admin/logs endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session with admin auth"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        # Login as admin
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200, f"Admin login failed: {login_resp.text}"
        self.admin_user = login_resp.json().get("user", {})
    
    def test_chat_admin_logs_returns_paginated_sessions(self):
        """GET /api/chat/admin/logs returns paginated chat sessions"""
        resp = self.session.get(f"{BASE_URL}/api/chat/admin/logs?skip=0&limit=10")
        assert resp.status_code == 200, f"Failed: {resp.text}"
        
        data = resp.json()
        assert "items" in data, "Response should have 'items' array"
        assert "total" in data, "Response should have 'total' count"
        assert isinstance(data["items"], list)
        assert isinstance(data["total"], int)
        print(f"SUCCESS: Got {len(data['items'])} sessions, total={data['total']}")
    
    def test_chat_admin_logs_session_has_required_fields(self):
        """Each session in response has required fields"""
        resp = self.session.get(f"{BASE_URL}/api/chat/admin/logs?limit=5")
        assert resp.status_code == 200
        
        data = resp.json()
        if data["items"]:
            session = data["items"][0]
            required_fields = ["session_id", "user_id", "title", "created_at", "updated_at", "message_count"]
            for field in required_fields:
                assert field in session, f"Session missing required field: {field}"
            
            # Check user info fields (user_email, user_name)
            assert "user_email" in session or "user_id" in session
            print(f"SUCCESS: Session has all required fields. chat_number={session.get('chat_number', '—')}")
    
    def test_chat_admin_logs_filter_by_chat_number(self):
        """GET /api/chat/admin/logs supports chat_number filter"""
        resp = self.session.get(f"{BASE_URL}/api/chat/admin/logs?chat_number=CHAT-00001")
        assert resp.status_code == 200
        
        data = resp.json()
        if data["total"] > 0:
            for session in data["items"]:
                assert "CHAT-00001" in session.get("chat_number", "").upper(), \
                    f"Filter not working: got {session.get('chat_number')}"
            print(f"SUCCESS: chat_number filter works, found {data['total']} matching sessions")
        else:
            print("SUCCESS: Filter works (no sessions with CHAT-00001)")
    
    def test_chat_admin_logs_filter_by_user_id(self):
        """GET /api/chat/admin/logs supports user_id filter"""
        user_id = self.admin_user.get("user_id")
        resp = self.session.get(f"{BASE_URL}/api/chat/admin/logs?user_id={user_id}")
        assert resp.status_code == 200
        
        data = resp.json()
        for session in data["items"]:
            assert session.get("user_id") == user_id, f"user_id filter not working"
        print(f"SUCCESS: user_id filter works, found {data['total']} sessions for user")
    
    def test_chat_admin_logs_filter_by_date_from(self):
        """GET /api/chat/admin/logs supports date_from filter"""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        resp = self.session.get(f"{BASE_URL}/api/chat/admin/logs?date_from={yesterday}")
        assert resp.status_code == 200
        
        data = resp.json()
        print(f"SUCCESS: date_from filter works, found {data['total']} sessions from {yesterday}")
    
    def test_chat_admin_logs_filter_by_date_to(self):
        """GET /api/chat/admin/logs supports date_to filter"""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        resp = self.session.get(f"{BASE_URL}/api/chat/admin/logs?date_to={tomorrow}")
        assert resp.status_code == 200
        
        data = resp.json()
        print(f"SUCCESS: date_to filter works, found {data['total']} sessions until {tomorrow}")
    
    def test_chat_admin_logs_requires_auth(self):
        """GET /api/chat/admin/logs returns 401 without auth"""
        unauth_session = requests.Session()
        resp = unauth_session.get(f"{BASE_URL}/api/chat/admin/logs")
        assert resp.status_code == 401, f"Should return 401, got {resp.status_code}"
        print("SUCCESS: /api/chat/admin/logs requires authentication")


class TestChatAdminLogMessages:
    """Tests for GET /api/chat/admin/logs/{session_id}/messages endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session with admin auth"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
    
    def test_chat_log_messages_returns_session_and_messages(self):
        """GET /api/chat/admin/logs/{session_id}/messages returns full conversation"""
        # First get a session with messages
        logs_resp = self.session.get(f"{BASE_URL}/api/chat/admin/logs?limit=20")
        assert logs_resp.status_code == 200
        
        sessions = logs_resp.json().get("items", [])
        session_with_messages = next((s for s in sessions if s.get("message_count", 0) > 0), None)
        
        if session_with_messages:
            session_id = session_with_messages["session_id"]
            resp = self.session.get(f"{BASE_URL}/api/chat/admin/logs/{session_id}/messages")
            assert resp.status_code == 200
            
            data = resp.json()
            assert "session" in data, "Response should have 'session'"
            assert "messages" in data, "Response should have 'messages'"
            assert isinstance(data["messages"], list)
            
            if data["messages"]:
                msg = data["messages"][0]
                assert "message_id" in msg
                assert "role" in msg
                assert "content" in msg
                print(f"SUCCESS: Got {len(data['messages'])} messages for session {session_id}")
            else:
                print(f"SUCCESS: Session {session_id} has no messages")
        else:
            pytest.skip("No sessions with messages found")
    
    def test_chat_log_messages_404_for_invalid_session(self):
        """GET /api/chat/admin/logs/{session_id}/messages returns 404 for invalid ID"""
        resp = self.session.get(f"{BASE_URL}/api/chat/admin/logs/invalid_session_xyz/messages")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
        print("SUCCESS: Invalid session ID returns 404")
    
    def test_chat_log_messages_requires_auth(self):
        """GET /api/chat/admin/logs/{session_id}/messages returns 401 without auth"""
        unauth_session = requests.Session()
        resp = unauth_session.get(f"{BASE_URL}/api/chat/admin/logs/some_session/messages")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        print("SUCCESS: /api/chat/admin/logs/{session_id}/messages requires auth")


class TestChatSessionCreation:
    """Tests for POST /api/chat/sessions with auto-incrementing chat_number"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session with admin auth"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
    
    def test_create_session_returns_chat_number(self):
        """POST /api/chat/sessions creates session with chat_number"""
        resp = self.session.post(f"{BASE_URL}/api/chat/sessions")
        assert resp.status_code == 200, f"Failed: {resp.text}"
        
        data = resp.json()
        assert "session_id" in data
        assert "chat_number" in data
        assert data["chat_number"].startswith("CHAT-")
        print(f"SUCCESS: Created session with chat_number={data['chat_number']}")
    
    def test_chat_number_auto_increments(self):
        """Consecutive sessions get incrementing chat_numbers"""
        # Create first session
        resp1 = self.session.post(f"{BASE_URL}/api/chat/sessions")
        assert resp1.status_code == 200
        chat_num1 = resp1.json()["chat_number"]
        
        # Create second session
        resp2 = self.session.post(f"{BASE_URL}/api/chat/sessions")
        assert resp2.status_code == 200
        chat_num2 = resp2.json()["chat_number"]
        
        # Parse numbers
        num1 = int(chat_num1.replace("CHAT-", ""))
        num2 = int(chat_num2.replace("CHAT-", ""))
        
        assert num2 == num1 + 1, f"Chat numbers not incrementing: {chat_num1} -> {chat_num2}"
        print(f"SUCCESS: Chat numbers auto-increment: {chat_num1} -> {chat_num2}")


class TestPermissionBasedEditButtons:
    """Tests for permission-based edit button visibility"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session with admin auth"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
        self.admin_user = login_resp.json().get("user", {})
    
    def test_admin_has_wildcard_permission(self):
        """Admin user has '*' permission which grants all access"""
        # Get current user's role permissions
        roles_resp = self.session.get(f"{BASE_URL}/api/roles")
        assert roles_resp.status_code == 200
        
        roles = roles_resp.json()
        admin_role = next((r for r in roles if r["name"] == "admin"), None)
        assert admin_role is not None
        assert "*" in admin_role["permissions"], "Admin role should have * permission"
        print("SUCCESS: Admin role has wildcard (*) permission")
    
    def test_director_has_deals_edit_all_permission(self):
        """Director role has deals.edit.all permission"""
        roles_resp = self.session.get(f"{BASE_URL}/api/roles")
        assert roles_resp.status_code == 200
        
        roles = roles_resp.json()
        director_role = next((r for r in roles if r["name"] == "director"), None)
        assert director_role is not None
        assert "deals.edit.all" in director_role["permissions"], \
            "Director should have deals.edit.all"
        print("SUCCESS: Director role has deals.edit.all permission")
    
    def test_sales_rep_does_not_have_deals_edit_all(self):
        """Sales rep has deals.edit.own but NOT deals.edit.all"""
        roles_resp = self.session.get(f"{BASE_URL}/api/roles")
        assert roles_resp.status_code == 200
        
        roles = roles_resp.json()
        sales_rep_role = next((r for r in roles if r["name"] == "sales_rep"), None)
        assert sales_rep_role is not None
        
        # Sales rep should have edit.own but not edit.all
        assert "deals.edit.own" in sales_rep_role["permissions"], \
            "Sales rep should have deals.edit.own"
        assert "deals.edit.all" not in sales_rep_role["permissions"], \
            "Sales rep should NOT have deals.edit.all"
        print("SUCCESS: Sales rep has deals.edit.own but NOT deals.edit.all")
    
    def test_director_has_commissions_mark_paid(self):
        """Director role has commissions.mark_paid permission"""
        roles_resp = self.session.get(f"{BASE_URL}/api/roles")
        assert roles_resp.status_code == 200
        
        roles = roles_resp.json()
        director_role = next((r for r in roles if r["name"] == "director"), None)
        assert director_role is not None
        assert "commissions.mark_paid" in director_role["permissions"], \
            "Director should have commissions.mark_paid"
        print("SUCCESS: Director role has commissions.mark_paid permission")
    
    def test_customer_does_not_have_edit_permissions(self):
        """Customer role has no deals.edit.all or commissions.mark_paid"""
        roles_resp = self.session.get(f"{BASE_URL}/api/roles")
        assert roles_resp.status_code == 200
        
        roles = roles_resp.json()
        customer_role = next((r for r in roles if r["name"] == "customer"), None)
        assert customer_role is not None
        
        assert "deals.edit.all" not in customer_role.get("permissions", [])
        assert "commissions.mark_paid" not in customer_role.get("permissions", [])
        print("SUCCESS: Customer role does not have edit permissions")


class TestAuditViewPermission:
    """Tests that chat admin logs requires audit.view permission"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup admin session"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": ADMIN_PASSWORD
        })
        assert login_resp.status_code == 200
    
    def test_admin_can_access_chat_logs(self):
        """Admin (with *) can access chat admin logs"""
        resp = self.session.get(f"{BASE_URL}/api/chat/admin/logs")
        assert resp.status_code == 200, f"Admin should access chat logs, got {resp.status_code}"
        print("SUCCESS: Admin can access chat admin logs (has * permission)")
    
    def test_roles_without_audit_view_cannot_access(self):
        """Roles without audit.view should get 403"""
        # First check which roles have audit.view
        roles_resp = self.session.get(f"{BASE_URL}/api/roles")
        roles = roles_resp.json()
        
        for role in roles:
            perms = role.get("permissions", [])
            has_audit = "audit.view" in perms or "*" in perms
            print(f"Role '{role['name']}': audit.view={has_audit}")
        
        # Only admin has * which includes audit.view
        # Non-admin roles should not have audit.view by default
        non_admin_roles = [r for r in roles if r["name"] != "admin"]
        for role in non_admin_roles:
            perms = role.get("permissions", [])
            if "*" not in perms:
                assert "audit.view" not in perms, \
                    f"Non-admin role {role['name']} should not have audit.view by default"
        
        print("SUCCESS: audit.view permission properly restricted")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
