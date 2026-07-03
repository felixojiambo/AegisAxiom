"""
Backend tests for AEGISAXIOM - Commission Workflow + RBAC System
Tests: Commission approve/reject/pay pipeline, report view, Roles & Permissions management
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")

# Admin credentials from problem statement
ADMIN_EMAIL = "support@simplimeta.io"
ADMIN_PASSWORD = "Admin@123"


@pytest.fixture(scope="session")
def session():
    """Create authenticated session"""
    s = requests.Session()
    resp = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["token"]
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="session")
def auth_token():
    """Get auth token"""
    resp = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return resp.json()["token"]


@pytest.fixture(scope="session")
def auth_headers(auth_token):
    """Auth headers"""
    return {"Authorization": f"Bearer {auth_token}"}


# =============================================
# COMMISSION WORKFLOW TESTS
# =============================================
class TestCommissionSummary:
    """Test commission summary endpoint - 4 cards on UI"""
    
    def test_summary_endpoint(self, auth_headers):
        """Verify summary returns total_owed, total_paid, total_potential, count"""
        r = requests.get(f"{BASE_URL}/api/commissions/summary", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "total_owed" in data, "Missing total_owed"
        assert "total_paid" in data, "Missing total_paid"
        assert "total_potential" in data, "Missing total_potential"
        assert "count" in data, "Missing count"
        print(f"Summary: owed={data['total_owed']}, paid={data['total_paid']}, potential={data['total_potential']}")


class TestCommissionList:
    """Test commission list with status filtering"""
    
    def test_list_commissions(self, auth_headers):
        """Get all commissions"""
        r = requests.get(f"{BASE_URL}/api/commissions", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        print(f"Total commissions: {len(data)}")
        
    def test_commission_has_status(self, auth_headers):
        """Verify commissions have status field for tab filtering"""
        r = requests.get(f"{BASE_URL}/api/commissions", headers=auth_headers)
        data = r.json()
        if len(data) > 0:
            comm = data[0]
            assert "status" in comm, "Commission missing status field"
            assert comm["status"] in ["pending", "approved", "paid", "rejected", "partial"]


class TestCommissionCreate:
    """Test creating a new commission"""
    
    def test_create_commission(self, auth_headers):
        """Create a new commission - should start as 'pending' status"""
        # First get a deal and user
        deals = requests.get(f"{BASE_URL}/api/deals", headers=auth_headers).json()
        users = requests.get(f"{BASE_URL}/api/admin/users", headers=auth_headers).json()
        
        if not deals or not users:
            pytest.skip("No deals or users available for test")
        
        deal_id = deals[0]["deal_id"]
        # Find an agent or sales_rep user
        test_user = next((u for u in users if u["role"] in ["agent", "sales_rep"]), users[0])
        user_id = test_user["user_id"]
        
        payload = {
            "deal_id": deal_id,
            "user_id": user_id,
            "commission_type": "agent",
            "amount_owed": 1500,
            "potential_amount": 2000,
            "commission_rate": 10,
            "notes": "TEST_Commission_Workflow"
        }
        r = requests.post(f"{BASE_URL}/api/commissions", headers=auth_headers, json=payload)
        assert r.status_code in [200, 201], f"Failed to create commission: {r.text}"
        data = r.json()
        
        # Verify new commission is pending
        assert data["status"] == "pending", f"New commission should be pending, got {data['status']}"
        assert data["commission_id"] is not None
        print(f"Created commission {data['commission_id']} with status={data['status']}")
        return data["commission_id"]


class TestCommissionApproveWorkflow:
    """Test approve -> pay workflow"""
    
    @pytest.fixture
    def test_commission(self, auth_headers):
        """Create a test commission for workflow tests"""
        deals = requests.get(f"{BASE_URL}/api/deals", headers=auth_headers).json()
        users = requests.get(f"{BASE_URL}/api/admin/users", headers=auth_headers).json()
        
        if not deals or not users:
            pytest.skip("No deals or users for workflow test")
            
        deal_id = deals[0]["deal_id"]
        user_id = users[0]["user_id"]
        
        payload = {
            "deal_id": deal_id,
            "user_id": user_id,
            "commission_type": "sales_rep",
            "amount_owed": 500,
            "potential_amount": 500,
            "commission_rate": 5,
            "notes": f"TEST_Workflow_{int(time.time())}"
        }
        r = requests.post(f"{BASE_URL}/api/commissions", headers=auth_headers, json=payload)
        assert r.status_code in [200, 201]
        return r.json()["commission_id"]
    
    def test_approve_commission(self, auth_headers, test_commission):
        """Approve a pending commission -> should become 'approved'"""
        comm_id = test_commission
        r = requests.post(f"{BASE_URL}/api/commissions/{comm_id}/approve", headers=auth_headers)
        assert r.status_code == 200, f"Approve failed: {r.text}"
        data = r.json()
        assert data["status"] == "approved", f"Expected approved, got {data['status']}"
        assert "approved_by" in data
        print(f"Commission {comm_id} approved successfully")
    
    def test_mark_paid_approved_commission(self, auth_headers):
        """Mark an approved commission as paid"""
        # Create and approve a commission
        deals = requests.get(f"{BASE_URL}/api/deals", headers=auth_headers).json()
        users = requests.get(f"{BASE_URL}/api/admin/users", headers=auth_headers).json()
        
        if not deals or not users:
            pytest.skip("No deals/users")
            
        payload = {
            "deal_id": deals[0]["deal_id"],
            "user_id": users[0]["user_id"],
            "commission_type": "agent",
            "amount_owed": 300,
            "notes": f"TEST_PayFlow_{int(time.time())}"
        }
        create_r = requests.post(f"{BASE_URL}/api/commissions", headers=auth_headers, json=payload)
        comm_id = create_r.json()["commission_id"]
        
        # Approve it
        approve_r = requests.post(f"{BASE_URL}/api/commissions/{comm_id}/approve", headers=auth_headers)
        assert approve_r.status_code == 200
        
        # Mark as paid
        pay_r = requests.post(f"{BASE_URL}/api/commissions/{comm_id}/pay", headers=auth_headers)
        assert pay_r.status_code == 200, f"Pay failed: {pay_r.text}"
        data = pay_r.json()
        assert data["status"] == "paid", f"Expected paid, got {data['status']}"
        assert data["amount_paid"] == data["amount_owed"], "Amount paid should equal amount owed"
        assert data["paid_date"] is not None
        print(f"Commission {comm_id} marked as paid")


class TestCommissionReject:
    """Test reject workflow"""
    
    def test_reject_commission_with_reason(self, auth_headers):
        """Reject a pending commission with reason"""
        # Create a commission
        deals = requests.get(f"{BASE_URL}/api/deals", headers=auth_headers).json()
        users = requests.get(f"{BASE_URL}/api/admin/users", headers=auth_headers).json()
        
        if not deals or not users:
            pytest.skip("No data for reject test")
            
        payload = {
            "deal_id": deals[0]["deal_id"],
            "user_id": users[0]["user_id"],
            "commission_type": "agent",
            "amount_owed": 200,
            "notes": f"TEST_RejectFlow_{int(time.time())}"
        }
        create_r = requests.post(f"{BASE_URL}/api/commissions", headers=auth_headers, json=payload)
        comm_id = create_r.json()["commission_id"]
        
        # Reject with reason
        reject_r = requests.post(
            f"{BASE_URL}/api/commissions/{comm_id}/reject", 
            headers=auth_headers,
            json={"reason": "TEST: Deal not verified"}
        )
        assert reject_r.status_code == 200, f"Reject failed: {reject_r.text}"
        data = reject_r.json()
        assert data["status"] == "rejected", f"Expected rejected, got {data['status']}"
        assert "rejected_by" in data
        print(f"Commission {comm_id} rejected with reason")


class TestCommissionReport:
    """Test commission report endpoint - by user breakdown"""
    
    def test_report_endpoint(self, auth_headers):
        """GET /api/commissions/report should return by_user breakdown"""
        r = requests.get(f"{BASE_URL}/api/commissions/report", headers=auth_headers)
        assert r.status_code == 200, f"Report failed: {r.text}"
        data = r.json()
        
        assert "by_user" in data, "Report missing by_user"
        assert "total_records" in data, "Report missing total_records"
        
        if len(data["by_user"]) > 0:
            user_entry = data["by_user"][0]
            # Verify columns match UI: Recipient, Count, Total Owed, Total Paid, Outstanding, Pending, Approved, Paid, Rejected
            assert "user_id" in user_entry
            assert "user_name" in user_entry
            assert "count" in user_entry
            assert "total_owed" in user_entry
            assert "total_paid" in user_entry
            # Status counts
            assert "pending" in user_entry
            assert "approved" in user_entry
            assert "paid" in user_entry
            assert "rejected" in user_entry
            print(f"Report has {len(data['by_user'])} user entries")


# =============================================
# ROLES & PERMISSIONS (RBAC) TESTS
# =============================================
class TestRolesList:
    """Test roles list endpoint"""
    
    def test_list_roles(self, auth_headers):
        """GET /api/roles should return default roles"""
        r = requests.get(f"{BASE_URL}/api/roles", headers=auth_headers)
        assert r.status_code == 200, f"List roles failed: {r.text}"
        roles = r.json()
        assert isinstance(roles, list)
        
        # Verify 5 default roles exist: admin, director, sales_rep, agent, customer
        role_names = [role["name"] for role in roles]
        expected_defaults = ["admin", "director", "sales_rep", "agent", "customer"]
        for expected in expected_defaults:
            assert expected in role_names, f"Missing default role: {expected}"
        print(f"Found {len(roles)} roles: {role_names}")
    
    def test_role_fields(self, auth_headers):
        """Verify role has required fields"""
        r = requests.get(f"{BASE_URL}/api/roles", headers=auth_headers)
        roles = r.json()
        role = roles[0]
        
        assert "role_id" in role
        assert "name" in role
        assert "display_name" in role
        assert "permissions" in role
        assert "color" in role


class TestPermissionsList:
    """Test permissions list endpoint"""
    
    def test_list_permissions(self, auth_headers):
        """GET /api/roles/permissions should return all available permissions"""
        r = requests.get(f"{BASE_URL}/api/roles/permissions", headers=auth_headers)
        assert r.status_code == 200, f"List permissions failed: {r.text}"
        perms = r.json()
        assert isinstance(perms, list)
        assert len(perms) > 0, "No permissions returned"
        
        # Verify permission structure
        perm = perms[0]
        assert "key" in perm
        assert "group" in perm
        assert "label" in perm
        
        # Check for expected permission groups
        groups = set(p["group"] for p in perms)
        expected_groups = ["Accounts", "Deals", "Commissions", "Invoices", "User Mgmt"]
        for grp in expected_groups:
            assert grp in groups, f"Missing permission group: {grp}"
        print(f"Found {len(perms)} permissions in {len(groups)} groups")


class TestRoleCRUD:
    """Test role CRUD operations"""
    
    def test_create_role(self, auth_headers):
        """Create a new custom role"""
        unique_name = f"test_role_{int(time.time())}"
        payload = {
            "name": unique_name,
            "display_name": "Test Custom Role",
            "description": "Created by automated test",
            "color": "#D97706",
            "permissions": ["deals.view.own", "accounts.view.own"]
        }
        r = requests.post(f"{BASE_URL}/api/roles", headers=auth_headers, json=payload)
        assert r.status_code in [200, 201], f"Create role failed: {r.text}"
        data = r.json()
        
        assert data["name"] == unique_name
        assert data["display_name"] == "Test Custom Role"
        assert data["is_system"] == False
        assert "deals.view.own" in data["permissions"]
        print(f"Created role {data['role_id']} with name={data['name']}")
        return data["role_id"]
    
    def test_update_role_permissions(self, auth_headers):
        """Update a role's permissions - verify 'Save Changes' workflow"""
        # Get director role (not system, can be modified)
        roles = requests.get(f"{BASE_URL}/api/roles", headers=auth_headers).json()
        director = next((r for r in roles if r["name"] == "director"), None)
        
        if not director:
            pytest.skip("Director role not found")
        
        # Toggle a permission (add if not present, or just verify update works)
        current_perms = director["permissions"]
        new_perm = "webhooks.view"
        
        if new_perm in current_perms:
            new_perms = [p for p in current_perms if p != new_perm]
        else:
            new_perms = current_perms + [new_perm]
        
        r = requests.put(
            f"{BASE_URL}/api/roles/{director['role_id']}", 
            headers=auth_headers,
            json={"permissions": new_perms}
        )
        assert r.status_code == 200, f"Update role failed: {r.text}"
        updated = r.json()
        
        if new_perm in current_perms:
            assert new_perm not in updated["permissions"]
        else:
            assert new_perm in updated["permissions"]
        print(f"Updated director role permissions successfully")
    
    def test_clone_role(self, auth_headers):
        """Clone an existing role"""
        # Clone the sales_rep role
        roles = requests.get(f"{BASE_URL}/api/roles", headers=auth_headers).json()
        sales_rep = next((r for r in roles if r["name"] == "sales_rep"), None)
        
        if not sales_rep:
            pytest.skip("sales_rep role not found")
        
        clone_name = f"sales_rep_clone_{int(time.time())}"
        r = requests.post(
            f"{BASE_URL}/api/roles/{sales_rep['role_id']}/clone",
            headers=auth_headers,
            json={"name": clone_name, "display_name": "Sales Rep Clone"}
        )
        assert r.status_code in [200, 201], f"Clone role failed: {r.text}"
        cloned = r.json()
        
        assert cloned["name"] == clone_name
        assert cloned["display_name"] == "Sales Rep Clone"
        # Cloned role should have same permissions as original
        assert set(cloned["permissions"]) == set(sales_rep["permissions"])
        print(f"Cloned role: {cloned['role_id']}")
    
    def test_cannot_modify_admin_role(self, auth_headers):
        """Admin (system) role cannot be modified"""
        roles = requests.get(f"{BASE_URL}/api/roles", headers=auth_headers).json()
        admin_role = next((r for r in roles if r["name"] == "admin"), None)
        
        if not admin_role:
            pytest.skip("Admin role not found")
        
        r = requests.put(
            f"{BASE_URL}/api/roles/{admin_role['role_id']}",
            headers=auth_headers,
            json={"permissions": ["deals.view.own"]}
        )
        assert r.status_code == 400, f"Should not be able to modify admin role: {r.text}"
        print("Correctly prevented modification of system admin role")


class TestAdminUsers:
    """Test admin user endpoints with RBAC"""
    
    def test_get_users_for_admin(self, auth_headers):
        """GET /api/admin/users should return users for admin"""
        r = requests.get(f"{BASE_URL}/api/admin/users", headers=auth_headers)
        assert r.status_code == 200, f"Get users failed: {r.text}"
        users = r.json()
        assert isinstance(users, list)
        
        # Verify user structure
        if len(users) > 0:
            user = users[0]
            assert "user_id" in user
            assert "email" in user
            assert "role" in user
            assert "password_hash" not in user, "Password hash should be excluded!"
        print(f"Admin can view {len(users)} users")


# =============================================
# EDGE CASES & ERROR HANDLING
# =============================================
class TestCommissionEdgeCases:
    """Edge case tests for commission workflow"""
    
    def test_approve_already_approved(self, auth_headers):
        """Cannot approve a commission that's already approved/paid"""
        # Create and approve
        deals = requests.get(f"{BASE_URL}/api/deals", headers=auth_headers).json()
        users = requests.get(f"{BASE_URL}/api/admin/users", headers=auth_headers).json()
        
        if not deals or not users:
            pytest.skip("No data")
            
        payload = {
            "deal_id": deals[0]["deal_id"],
            "user_id": users[0]["user_id"],
            "amount_owed": 100,
            "notes": f"TEST_EdgeCase_{int(time.time())}"
        }
        create_r = requests.post(f"{BASE_URL}/api/commissions", headers=auth_headers, json=payload)
        comm_id = create_r.json()["commission_id"]
        
        # First approve succeeds
        approve1 = requests.post(f"{BASE_URL}/api/commissions/{comm_id}/approve", headers=auth_headers)
        assert approve1.status_code == 200
        
        # Second approve should fail
        approve2 = requests.post(f"{BASE_URL}/api/commissions/{comm_id}/approve", headers=auth_headers)
        assert approve2.status_code == 400, "Should not be able to approve already-approved commission"
        print("Correctly prevented double-approve")
    
    def test_commission_not_found(self, auth_headers):
        """Approve non-existent commission returns 404"""
        r = requests.post(f"{BASE_URL}/api/commissions/fake_id_12345/approve", headers=auth_headers)
        assert r.status_code == 404
        print("Correctly returned 404 for non-existent commission")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
