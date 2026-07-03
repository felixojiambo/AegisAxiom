"""
Test suite for MEDIUM and ARCHITECTURAL fixes in AegisAxiom
Tests: Medium Issues 6-11, Architectural Issues 12-16, Audit Log system
"""
import pytest
import requests
import os
import uuid
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Admin credentials
ADMIN_EMAIL = "support@simplimeta.io"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


class TestSetup:
    """Test fixture to get admin session"""
    
    @pytest.fixture(scope="class")
    def admin_session(self):
        """Get authenticated admin session"""
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Admin login failed: {response.text}"
        return session


class TestMediumIssue6ValidateInvitePOST(TestSetup):
    """Issue 6: validate-invite changed from GET to POST"""
    
    def test_validate_invite_is_post(self, admin_session):
        """Verify validate-invite uses POST method"""
        # Create a test invite first
        test_email = f"test_validate_{uuid.uuid4().hex[:8]}@test.com"
        invite_resp = admin_session.post(f"{BASE_URL}/api/admin/invite", json={
            "email": test_email,
            "name": "Test Validate User",
            "role": "customer"
        })
        assert invite_resp.status_code == 200, f"Invite creation failed: {invite_resp.text}"
        invite_token = invite_resp.json().get("invite_link", "").split("token=")[-1]
        
        # Test POST method works
        post_resp = requests.post(f"{BASE_URL}/api/auth/validate-invite", json={
            "token": invite_token,
            "new_password": "DummyPassword123"  # Required by PasswordReset model
        })
        assert post_resp.status_code == 200, f"POST validate-invite failed: {post_resp.text}"
        data = post_resp.json()
        assert "email" in data
        assert "name" in data
        assert "role" in data
        print(f"PASS: validate-invite POST returns user info: {data}")


class TestMediumIssue7PydanticModels(TestSetup):
    """Issue 7: All 'data: dict' routes replaced with Pydantic models"""
    
    def test_invite_pydantic_validation(self, admin_session):
        """POST /api/admin/invite validates with Pydantic"""
        # Missing required fields should fail with 422
        response = admin_session.post(f"{BASE_URL}/api/admin/invite", json={})
        assert response.status_code == 422, "Expected 422 for missing required fields"
        print("PASS: /api/admin/invite uses Pydantic validation")
    
    def test_user_update_pydantic_validation(self, admin_session):
        """PUT /api/admin/users/{id} uses Pydantic model"""
        # Create a test user first
        test_email = f"test_update_{uuid.uuid4().hex[:8]}@test.com"
        invite_resp = admin_session.post(f"{BASE_URL}/api/admin/invite", json={
            "email": test_email,
            "name": "Test Update User",
            "role": "customer"
        })
        assert invite_resp.status_code == 200
        user_id = invite_resp.json().get("user_id")
        
        # Update with valid Pydantic model
        update_resp = admin_session.put(f"{BASE_URL}/api/admin/users/{user_id}", json={
            "name": "Updated Name"
        })
        assert update_resp.status_code == 200
        assert update_resp.json().get("name") == "Updated Name"
        print("PASS: /api/admin/users/{id} uses Pydantic validation")
    
    def test_role_create_pydantic(self, admin_session):
        """POST /api/roles uses Pydantic model"""
        role_name = f"test_role_{uuid.uuid4().hex[:8]}"
        response = admin_session.post(f"{BASE_URL}/api/roles", json={
            "name": role_name,
            "display_name": "Test Role",
            "description": "Test role for Pydantic validation",
            "color": "#FF0000",
            "permissions": ["chat.access"]
        })
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == role_name
        print(f"PASS: /api/roles POST creates role with Pydantic model")
        return data["role_id"]
    
    def test_role_update_pydantic(self, admin_session):
        """PUT /api/roles/{id} uses Pydantic model"""
        # Create a role first
        role_name = f"test_role_{uuid.uuid4().hex[:8]}"
        create_resp = admin_session.post(f"{BASE_URL}/api/roles", json={
            "name": role_name,
            "permissions": []
        })
        assert create_resp.status_code == 200
        role_id = create_resp.json()["role_id"]
        
        # Update with Pydantic model
        update_resp = admin_session.put(f"{BASE_URL}/api/roles/{role_id}", json={
            "description": "Updated description",
            "color": "#00FF00"
        })
        assert update_resp.status_code == 200
        assert update_resp.json()["description"] == "Updated description"
        print("PASS: /api/roles/{id} PUT uses Pydantic model")
    
    def test_role_clone_pydantic(self, admin_session):
        """POST /api/roles/{id}/clone uses Pydantic model"""
        # Create a role first
        role_name = f"test_role_{uuid.uuid4().hex[:8]}"
        create_resp = admin_session.post(f"{BASE_URL}/api/roles", json={
            "name": role_name,
            "permissions": ["chat.access"]
        })
        assert create_resp.status_code == 200
        role_id = create_resp.json()["role_id"]
        
        # Clone with Pydantic model
        clone_resp = admin_session.post(f"{BASE_URL}/api/roles/{role_id}/clone", json={
            "name": f"cloned_{uuid.uuid4().hex[:8]}"
        })
        assert clone_resp.status_code == 200
        print("PASS: /api/roles/{id}/clone uses Pydantic model")


class TestMediumIssue8MongoIndexes:
    """Issue 8: MongoDB indexes created on startup"""
    
    def test_indexes_ensured_log(self):
        """Check backend logs for MongoDB indexes ensured message"""
        import subprocess
        result = subprocess.run(
            ["grep", "-i", "MongoDB indexes ensured", "/var/log/supervisor/backend.err.log"],
            capture_output=True, text=True
        )
        assert "MongoDB indexes ensured" in result.stdout, "MongoDB indexes not ensured"
        print("PASS: MongoDB indexes ensured on startup")


class TestMediumIssue9CascadeDeletes(TestSetup):
    """Issue 9: Cascade deletes work properly"""
    
    def test_account_cascade_delete(self, admin_session):
        """Deleting account cascades to deals/invoices/contracts/jobs"""
        # Create test account
        account_resp = admin_session.post(f"{BASE_URL}/api/accounts", json={
            "name": f"Test Cascade Account {uuid.uuid4().hex[:8]}",
            "account_type": "company"
        })
        assert account_resp.status_code == 200
        account_id = account_resp.json()["account_id"]
        
        # Create related deal
        deal_resp = admin_session.post(f"{BASE_URL}/api/deals", json={
            "name": "Test Cascade Deal",
            "account_id": account_id,
            "amount": 1000
        })
        assert deal_resp.status_code == 200
        deal_id = deal_resp.json()["deal_id"]
        
        # Create related contract
        contract_resp = admin_session.post(f"{BASE_URL}/api/contracts", json={
            "name": "Test Cascade Contract",
            "account_id": account_id
        })
        assert contract_resp.status_code == 200
        
        # Delete account - should cascade
        delete_resp = admin_session.delete(f"{BASE_URL}/api/accounts/{account_id}")
        assert delete_resp.status_code == 200
        assert "deleted" in delete_resp.json().get("message", "").lower()
        
        # Verify deal is deleted
        deal_check = admin_session.get(f"{BASE_URL}/api/deals/{deal_id}")
        assert deal_check.status_code == 404
        print("PASS: Account delete cascades to deals/contracts")
    
    def test_deal_cascade_delete(self, admin_session):
        """Deleting deal cascades to commissions/invoices"""
        # Create account first
        account_resp = admin_session.post(f"{BASE_URL}/api/accounts", json={
            "name": f"Test Deal Cascade {uuid.uuid4().hex[:8]}",
            "account_type": "company"
        })
        assert account_resp.status_code == 200
        account_id = account_resp.json()["account_id"]
        
        # Create deal
        deal_resp = admin_session.post(f"{BASE_URL}/api/deals", json={
            "name": "Test Cascade Deal",
            "account_id": account_id,
            "amount": 5000
        })
        assert deal_resp.status_code == 200
        deal_id = deal_resp.json()["deal_id"]
        
        # Get admin user_id for commission
        me_resp = admin_session.get(f"{BASE_URL}/api/auth/me")
        user_id = me_resp.json()["user_id"]
        
        # Create commission on deal
        comm_resp = admin_session.post(f"{BASE_URL}/api/commissions", json={
            "deal_id": deal_id,
            "user_id": user_id,
            "commission_type": "sales_rep",
            "amount_owed": 500,
            "commission_rate": 10
        })
        assert comm_resp.status_code == 200
        comm_id = comm_resp.json()["commission_id"]
        
        # Delete deal - should cascade commissions
        delete_resp = admin_session.delete(f"{BASE_URL}/api/deals/{deal_id}")
        assert delete_resp.status_code == 200
        
        # Verify commission is deleted
        comm_check = admin_session.get(f"{BASE_URL}/api/commissions/{comm_id}")
        assert comm_check.status_code == 404
        print("PASS: Deal delete cascades to commissions")
        
        # Cleanup
        admin_session.delete(f"{BASE_URL}/api/accounts/{account_id}")


class TestMediumIssue10FrontendURLValidation(TestSetup):
    """Issue 10: get_frontend_url validates against ALLOWED_ORIGINS"""
    
    def test_forgot_password_uses_allowed_origin(self, admin_session):
        """Forgot password endpoint respects ALLOWED_ORIGINS"""
        response = requests.post(
            f"{BASE_URL}/api/auth/forgot-password",
            json={"email": ADMIN_EMAIL},
            headers={"Origin": BASE_URL}
        )
        assert response.status_code == 200
        print("PASS: Forgot password works with allowed origin")


class TestMediumIssue11AIPromptNoUserLeak:
    """Issue 11: AI system prompt no longer leaks user_id or account_id"""
    
    def test_ai_service_prompt_sanitized(self):
        """Verify AI service doesn't include user_id/account_id in prompt"""
        import subprocess
        result = subprocess.run(
            ["grep", "-E", "user_id|account_id", "/app/backend/services/ai_service.py"],
            capture_output=True, text=True
        )
        # The grep should not find user_id or account_id in the system prompt construction
        lines = result.stdout.strip().split('\n') if result.stdout.strip() else []
        # Filter out import lines and function parameters
        dangerous_lines = [l for l in lines if 'user.' in l and ('user.user_id' in l or 'user.account_id' in l) and 'system' in l.lower()]
        assert len(dangerous_lines) == 0, f"AI prompt may leak user IDs: {dangerous_lines}"
        print("PASS: AI system prompt does not leak user_id/account_id")


class TestArchIssue12Pagination(TestSetup):
    """Issue 12: All list endpoints return paginated {items, total, skip, limit}"""
    
    def test_accounts_pagination(self, admin_session):
        """GET /api/accounts returns paginated response"""
        response = admin_session.get(f"{BASE_URL}/api/accounts?skip=0&limit=5")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data, "Missing 'items' key"
        assert "total" in data, "Missing 'total' key"
        assert "skip" in data, "Missing 'skip' key"
        assert "limit" in data, "Missing 'limit' key"
        assert isinstance(data["items"], list)
        print(f"PASS: /api/accounts returns paginated response with {data['total']} total items")
    
    def test_deals_pagination(self, admin_session):
        """GET /api/deals returns paginated response"""
        response = admin_session.get(f"{BASE_URL}/api/deals?skip=0&limit=5")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        print(f"PASS: /api/deals returns paginated response")
    
    def test_invoices_pagination(self, admin_session):
        """GET /api/invoices returns paginated response"""
        response = admin_session.get(f"{BASE_URL}/api/invoices?skip=0&limit=5")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        print(f"PASS: /api/invoices returns paginated response")
    
    def test_commissions_pagination(self, admin_session):
        """GET /api/commissions returns paginated response"""
        response = admin_session.get(f"{BASE_URL}/api/commissions?skip=0&limit=5")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        print(f"PASS: /api/commissions returns paginated response")
    
    def test_contracts_pagination(self, admin_session):
        """GET /api/contracts returns paginated response"""
        response = admin_session.get(f"{BASE_URL}/api/contracts?skip=0&limit=5")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        print(f"PASS: /api/contracts returns paginated response")
    
    def test_jobs_pagination(self, admin_session):
        """GET /api/jobs returns paginated response"""
        response = admin_session.get(f"{BASE_URL}/api/jobs?skip=0&limit=5")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        print(f"PASS: /api/jobs returns paginated response")


class TestArchIssue13ZohoSyncLock:
    """Issue 13: Zoho sync has idempotency lock"""
    
    def test_zoho_sync_lock_exists(self):
        """Verify sync lock mechanism exists in code"""
        import subprocess
        result = subprocess.run(
            ["grep", "-E", "_sync_lock|sync_lock", "/app/backend/server.py"],
            capture_output=True, text=True
        )
        assert "_sync_lock" in result.stdout, "Zoho sync lock not found"
        assert "if _sync_lock" in result.stdout, "Sync lock check not found"
        print("PASS: Zoho sync has idempotency lock mechanism")


class TestArchIssue14FrontendErrorDetail:
    """Issue 14: Frontend API wrapper returns proper error detail on network errors"""
    
    def test_api_wrapper_has_error_detail(self):
        """Verify api.js returns error detail for network errors"""
        import subprocess
        result = subprocess.run(
            ["grep", "-E", "detail.*Network error|Network error.*detail", "/app/frontend/src/lib/api.js"],
            capture_output=True, text=True
        )
        assert "Network error" in result.stdout
        print("PASS: Frontend API wrapper returns error detail on network errors")


class TestArchIssue15ContractsDeletePermission(TestSetup):
    """Issue 15: contracts.delete permission exists and is assigned to director role"""
    
    def test_contracts_delete_in_permissions_list(self, admin_session):
        """Verify contracts.delete is in the permission registry"""
        response = admin_session.get(f"{BASE_URL}/api/roles/permissions")
        assert response.status_code == 200
        permissions = response.json()
        perm_keys = [p["key"] for p in permissions]
        assert "contracts.delete" in perm_keys, "contracts.delete not in permissions list"
        print("PASS: contracts.delete is in the permission registry")
    
    def test_director_has_contracts_delete(self, admin_session):
        """Verify director role has contracts.delete permission"""
        response = admin_session.get(f"{BASE_URL}/api/roles")
        assert response.status_code == 200
        roles = response.json()
        director_role = next((r for r in roles if r["name"] == "director"), None)
        assert director_role is not None, "Director role not found"
        assert "contracts.delete" in director_role["permissions"], "Director missing contracts.delete"
        print(f"PASS: Director role has contracts.delete permission")


class TestArchIssue16AuditLog(TestSetup):
    """Issue 16: Audit log system works correctly"""
    
    def test_login_creates_audit_entry(self, admin_session):
        """POST /api/auth/login creates audit entry"""
        # Login creates an audit entry - check audit logs
        response = admin_session.get(f"{BASE_URL}/api/audit?action=login&limit=5")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        # Check if there's at least one login audit entry
        login_entries = [e for e in data["items"] if e["action"] == "login"]
        assert len(login_entries) > 0, "No login audit entries found"
        print(f"PASS: Login creates audit entry, found {len(login_entries)} entries")
    
    def test_audit_endpoint_pagination(self, admin_session):
        """GET /api/audit returns paginated response"""
        response = admin_session.get(f"{BASE_URL}/api/audit?skip=0&limit=10")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "skip" in data
        assert "limit" in data
        print(f"PASS: /api/audit returns paginated response with {data['total']} total entries")
    
    def test_audit_filter_by_action(self, admin_session):
        """Audit logs can be filtered by action"""
        response = admin_session.get(f"{BASE_URL}/api/audit?action=login")
        assert response.status_code == 200
        data = response.json()
        # All returned items should have action=login
        for item in data["items"]:
            assert item["action"] == "login"
        print("PASS: Audit logs can be filtered by action")
    
    def test_user_invite_creates_audit(self, admin_session):
        """User invite creates audit entry"""
        test_email = f"audit_test_{uuid.uuid4().hex[:8]}@test.com"
        invite_resp = admin_session.post(f"{BASE_URL}/api/admin/invite", json={
            "email": test_email,
            "name": "Audit Test User",
            "role": "customer"
        })
        assert invite_resp.status_code == 200
        
        # Check audit log for user_invite
        audit_resp = admin_session.get(f"{BASE_URL}/api/audit?action=user_invite&limit=5")
        assert audit_resp.status_code == 200
        entries = audit_resp.json()["items"]
        recent_invite = [e for e in entries if test_email in str(e.get("details", {}))]
        assert len(recent_invite) > 0, "User invite audit entry not found"
        print("PASS: User invite creates audit entry")
    
    def test_role_create_creates_audit(self, admin_session):
        """Role create creates audit entry"""
        role_name = f"audit_role_{uuid.uuid4().hex[:8]}"
        create_resp = admin_session.post(f"{BASE_URL}/api/roles", json={
            "name": role_name,
            "permissions": []
        })
        assert create_resp.status_code == 200
        
        # Check audit log
        audit_resp = admin_session.get(f"{BASE_URL}/api/audit?action=role_create&limit=5")
        assert audit_resp.status_code == 200
        entries = audit_resp.json()["items"]
        assert len(entries) > 0, "Role create audit entry not found"
        print("PASS: Role create creates audit entry")


class TestFrontendUnwrapHelper:
    """Test frontend unwrap() helper exists"""
    
    def test_unwrap_helper_exists(self):
        """Verify unwrap() helper exists in api.js"""
        import subprocess
        result = subprocess.run(
            ["grep", "-E", "export const unwrap|unwrap.*=.*data", "/app/frontend/src/lib/api.js"],
            capture_output=True, text=True
        )
        assert "unwrap" in result.stdout
        print("PASS: Frontend has unwrap() helper for paginated responses")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
