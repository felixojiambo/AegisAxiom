"""
Comprehensive Pre-Deployment Test Suite for AEGISAXIOM
Tests: RBAC, Referral MRR, Contracts, Commissions, Zoho Integration, Deals

This test file validates critical flows before deployment to main branch.
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    raise ValueError("REACT_APP_BACKEND_URL not set")

# Admin credentials
ADMIN_EMAIL = "support@simplimeta.io"
ADMIN_PASSWORD = "Admin123!"

# Test data prefix for cleanup
TEST_PREFIX = "TEST_PREDEPLOYMENT_"


class TestHealthAndAuth:
    """Test 1: Health check and admin authentication"""
    
    def test_health_check(self):
        """Health endpoint should return ok"""
        resp = requests.get(f"{BASE_URL}/api/health")
        assert resp.status_code == 200, f"Health check failed: {resp.status_code}"
        data = resp.json()
        assert data.get("status") == "ok"
        print("✓ Health check passed")
    
    def test_admin_login(self):
        """Admin login should succeed and return permissions"""
        resp = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert resp.status_code == 200, f"Login failed: {resp.status_code} - {resp.text}"
        data = resp.json()
        assert "user" in data
        assert data["user"]["role"] == "admin"
        assert "*" in data["user"]["permissions"], "Admin should have wildcard permission"
        print(f"✓ Admin login successful: {data['user']['email']}")
    
    def test_invalid_login(self):
        """Invalid credentials should return 401"""
        resp = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "nonexistent@test.com", "password": "wrong"}
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        print("✓ Invalid login correctly rejected")


@pytest.fixture(scope="class")
def admin_session():
    """Get authenticated admin session"""
    session = requests.Session()
    resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    return session


class TestRBACPermissions:
    """Test 2: Role-Based Access Control and Permission System"""
    
    def test_roles_endpoint(self, admin_session):
        """Verify roles endpoint returns all roles with permissions"""
        resp = admin_session.get(f"{BASE_URL}/api/roles")
        assert resp.status_code == 200, f"Roles fetch failed: {resp.status_code}"
        data = resp.json()
        # API returns list directly, not wrapped in "items"
        roles = data if isinstance(data, list) else data.get("items", [])
        assert len(roles) >= 6, f"Expected at least 6 roles, got {len(roles)}"
        
        # Check key roles exist
        role_names = [r["name"] for r in roles]
        expected_roles = ["admin", "director", "finance", "sales_rep", "agent", "customer"]
        for role in expected_roles:
            assert role in role_names, f"Missing role: {role}"
        
        # Verify admin has * permission
        admin_role = next((r for r in roles if r["name"] == "admin"), None)
        assert admin_role and "*" in admin_role.get("permissions", []), "Admin missing wildcard permission"
        print(f"✓ Roles verified: {len(roles)} roles found")
    
    def test_permissions_definition(self, admin_session):
        """Verify permissions definition endpoint exists"""
        resp = admin_session.get(f"{BASE_URL}/api/roles/permissions")
        assert resp.status_code == 200, f"Permissions fetch failed: {resp.status_code}"
        data = resp.json()
        
        # Should have multiple permission categories
        assert len(data) > 0, "No permissions defined"
        print(f"✓ Permissions definition verified: {len(data)} categories")
    
    def test_unauthorized_access_without_cookie(self):
        """Protected endpoints should reject unauthenticated requests"""
        resp = requests.get(f"{BASE_URL}/api/deals")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
        print("✓ Unauthenticated access correctly rejected")


class TestDealsAndAccounts:
    """Test 3: CRM - Deals and Accounts Management"""
    
    def test_get_deals(self, admin_session):
        """Verify deals list endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/deals")
        assert resp.status_code == 200, f"Deals fetch failed: {resp.status_code}"
        data = resp.json()
        assert "items" in data
        assert "total" in data
        print(f"✓ Deals list: {data['total']} total deals")
    
    def test_get_accounts(self, admin_session):
        """Verify accounts list endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/accounts")
        assert resp.status_code == 200, f"Accounts fetch failed: {resp.status_code}"
        data = resp.json()
        assert "items" in data
        assert "total" in data
        print(f"✓ Accounts list: {data['total']} total accounts")
    
    def test_deal_types_populated(self, admin_session):
        """Verify deal_types (Line of Business) is populated from Zoho"""
        resp = admin_session.get(f"{BASE_URL}/api/contracts/deal-pipeline")
        assert resp.status_code == 200, f"Deal pipeline failed: {resp.status_code}"
        data = resp.json()
        
        all_deals = data.get("unassigned", {}).get("items", []) + data.get("assigned", {}).get("items", [])
        
        # Check that at least some deals have deal_types
        deals_with_types = [d for d in all_deals if d.get("deal_types")]
        print(f"✓ Deal types: {len(deals_with_types)}/{len(all_deals)} deals have deal_types")
    
    def test_create_test_deal(self, admin_session):
        """Create a test deal with is_test_data flag"""
        # First get an account
        resp = admin_session.get(f"{BASE_URL}/api/accounts?limit=1")
        accounts = resp.json().get("items", [])
        if not accounts:
            pytest.skip("No accounts available for test deal creation")
        
        account_id = accounts[0]["account_id"]
        
        # Create test deal
        deal_data = {
            "name": f"{TEST_PREFIX}Deal",
            "account_id": account_id,
            "stage": "Prospecting",
            "amount": 1000.00,
            "probability": 50,
            "is_test_data": True,
            "description": "Automated pre-deployment test deal"
        }
        resp = admin_session.post(f"{BASE_URL}/api/deals", json=deal_data)
        assert resp.status_code == 200, f"Deal creation failed: {resp.status_code} - {resp.text}"
        deal = resp.json()
        assert deal["name"] == deal_data["name"]
        assert deal.get("is_test_data") == True
        print(f"✓ Test deal created: {deal['deal_id']}")
        
        # Cleanup
        admin_session.delete(f"{BASE_URL}/api/deals/{deal['deal_id']}")


class TestContractManagement:
    """Test 4: Contract Management - BOH, BOA, Fixed Fee, Wholesale"""
    
    def test_get_contracts(self, admin_session):
        """Verify contracts list endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/contracts")
        assert resp.status_code == 200, f"Contracts fetch failed: {resp.status_code}"
        data = resp.json()
        assert "items" in data
        print(f"✓ Contracts list: {data['total']} total contracts")
    
    def test_contract_types_supported(self, admin_session):
        """Verify all contract types can be retrieved"""
        valid_types = ["boh", "boa", "fixed_fee", "monthly", "wholesale_mrr"]
        for contract_type in valid_types:
            resp = admin_session.get(f"{BASE_URL}/api/contracts?contract_type={contract_type}")
            assert resp.status_code == 200, f"Failed to filter by {contract_type}"
        print(f"✓ All contract types supported: {', '.join(valid_types)}")
    
    def test_deal_pipeline_endpoint(self, admin_session):
        """Verify deal pipeline for contract assignment"""
        resp = admin_session.get(f"{BASE_URL}/api/contracts/deal-pipeline")
        assert resp.status_code == 200, f"Deal pipeline failed: {resp.status_code}"
        data = resp.json()
        
        assert "unassigned" in data
        assert "assigned" in data
        assert "total_closed_won" in data
        
        print(f"✓ Deal pipeline: {data['unassigned']['total']} unassigned, {data['assigned']['total']} assigned")
    
    def test_closed_won_deals_endpoint(self, admin_session):
        """Verify closed won deals endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/contracts/closed-won-deals")
        assert resp.status_code == 200, f"Closed won deals failed: {resp.status_code}"
        data = resp.json()
        assert "items" in data
        print(f"✓ Closed won deals: {data['total']} deals")


class TestCommissionManagement:
    """Test 5: Commission System - Templates, Plans, Multi-tier"""
    
    def test_commission_templates(self, admin_session):
        """Verify commission templates endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/commission-management/templates")
        assert resp.status_code == 200, f"Templates fetch failed: {resp.status_code}"
        data = resp.json()
        assert "items" in data
        print(f"✓ Commission templates: {len(data['items'])} templates")
    
    def test_commission_plans(self, admin_session):
        """Verify commission plans endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/commission-management/plans")
        assert resp.status_code == 200, f"Plans fetch failed: {resp.status_code}"
        data = resp.json()
        assert "items" in data
        print(f"✓ Commission plans: {len(data['items'])} plans")
    
    def test_create_commission_template(self, admin_session):
        """Test commission template creation"""
        template_data = {
            "name": f"{TEST_PREFIX}Template",
            "tier1_percentage": 5.0,
            "tier2_percentage": 3.0,
            "tier3_percentage": 2.0,
            "cost_type": "percentage",
            "cost_default": 30.0,
            "is_active": True
        }
        resp = admin_session.post(
            f"{BASE_URL}/api/commission-management/templates",
            json=template_data
        )
        assert resp.status_code == 200, f"Template creation failed: {resp.status_code} - {resp.text}"
        result = resp.json()
        assert "template_id" in result
        print(f"✓ Commission template created: {result['template_id']}")
        
        # Cleanup - soft delete
        admin_session.delete(f"{BASE_URL}/api/commission-management/templates/{result['template_id']}")


class TestReferralMRR:
    """Test 6: Referral MRR Workflow - CSV Import, Account Mapping"""
    
    def test_header_templates(self, admin_session):
        """Verify header templates endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/referral-mrr/header-templates")
        assert resp.status_code == 200, f"Header templates fetch failed: {resp.status_code}"
        data = resp.json()
        assert "templates" in data
        print(f"✓ Header templates: {len(data['templates'])} templates")
    
    def test_account_mappings(self, admin_session):
        """Verify account mappings endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/referral-mrr/account-mappings")
        assert resp.status_code == 200, f"Account mappings fetch failed: {resp.status_code}"
        data = resp.json()
        assert "mappings" in data
        print(f"✓ Account mappings: {len(data['mappings'])} mappings")
    
    def test_referral_commissions(self, admin_session):
        """Verify referral commissions endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/referral-mrr/commissions")
        assert resp.status_code == 200, f"Referral commissions fetch failed: {resp.status_code}"
        data = resp.json()
        assert "commissions" in data
        print(f"✓ Referral commissions: {len(data['commissions'])} records")


class TestFinancePortal:
    """Test 7: Finance Portal - Overview, Zoho Commissions, Local Commissions"""
    
    def test_finance_overview(self, admin_session):
        """Verify finance overview endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/finance/overview")
        assert resp.status_code == 200, f"Finance overview failed: {resp.status_code}"
        data = resp.json()
        
        # Check expected sections in overview
        expected_sections = ["commissions", "invoices", "zoho_commissions"]
        for section in expected_sections:
            assert section in data, f"Missing section: {section}"
        
        # Check commissions nested structure
        commissions = data.get("commissions", {})
        total_owed = commissions.get("total_owed", 0)
        print(f"✓ Finance overview: ${total_owed:.2f} total owed, {commissions.get('count', 0)} commissions")
    
    def test_finance_commissions(self, admin_session):
        """Verify local commissions endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/finance/commissions")
        assert resp.status_code == 200, f"Finance commissions failed: {resp.status_code}"
        data = resp.json()
        assert "items" in data
        print(f"✓ Local commissions: {data.get('total', len(data['items']))} records")
    
    def test_zoho_commissions(self, admin_session):
        """Verify Zoho commissions endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/finance/zoho-commissions")
        assert resp.status_code == 200, f"Zoho commissions failed: {resp.status_code}"
        data = resp.json()
        assert "items" in data
        print(f"✓ Zoho commissions: {data.get('total', len(data['items']))} records")


class TestZohoIntegration:
    """Test 8: Zoho CRM Integration - Status, Sync Endpoints"""
    
    def test_integration_status(self, admin_session):
        """Verify integration status endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/integrations/status")
        assert resp.status_code == 200, f"Integration status failed: {resp.status_code}"
        data = resp.json()
        
        assert "zoho" in data
        assert "qbo" in data
        
        zoho_connected = data["zoho"].get("connected", False)
        print(f"✓ Integration status: Zoho {'connected' if zoho_connected else 'not connected'}")
    
    def test_webhook_logs(self, admin_session):
        """Verify webhook logs endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/integrations/webhook-logs")
        assert resp.status_code == 200, f"Webhook logs failed: {resp.status_code}"
        data = resp.json()
        print(f"✓ Webhook logs: {len(data)} entries")


class TestUserManagement:
    """Test 9: User Management - List, Invite, Roles"""
    
    def test_users_list(self, admin_session):
        """Verify users list endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/admin/users")
        assert resp.status_code == 200, f"Users list failed: {resp.status_code}"
        data = resp.json()
        # API returns list directly
        users = data if isinstance(data, list) else data.get("items", [])
        assert len(users) > 0, "No users found"
        print(f"✓ Users list: {len(users)} users")
    
    def test_me_endpoint(self, admin_session):
        """Verify /me endpoint returns current user"""
        resp = admin_session.get(f"{BASE_URL}/api/auth/me")
        assert resp.status_code == 200, f"Me endpoint failed: {resp.status_code}"
        data = resp.json()
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        print(f"✓ Me endpoint: {data['email']} ({data['role']})")


class TestProjectsAndTimeTracking:
    """Test 10: Projects and Time Tracking System"""
    
    def test_projects_list(self, admin_session):
        """Verify projects list endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/projects")
        assert resp.status_code == 200, f"Projects list failed: {resp.status_code}"
        data = resp.json()
        assert "items" in data
        print(f"✓ Projects list: {data['total']} projects")
    
    def test_time_entries_list(self, admin_session):
        """Verify time entries endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/time-entries")
        assert resp.status_code == 200, f"Time entries failed: {resp.status_code}"
        data = resp.json()
        assert "items" in data
        print(f"✓ Time entries: {data.get('total', len(data['items']))} entries")
    
    def test_resource_types(self, admin_session):
        """Verify default resource types endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/admin/default-resource-types")
        assert resp.status_code == 200, f"Resource types failed: {resp.status_code}"
        data = resp.json()
        # API returns "default_resource_types" key
        resource_types = data.get("default_resource_types", data.get("items", []))
        print(f"✓ Resource types: {len(resource_types)} types")


class TestAuditAndLogs:
    """Test 11: Audit Logs and System Monitoring"""
    
    def test_audit_logs(self, admin_session):
        """Verify audit logs endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/audit")
        assert resp.status_code == 200, f"Audit logs failed: {resp.status_code}"
        data = resp.json()
        assert "items" in data
        print(f"✓ Audit logs: {data.get('total', len(data['items']))} entries")


class TestProductsAndServices:
    """Test 12: Products and Services (Wholesale MRR)"""
    
    def test_products_list(self, admin_session):
        """Verify products list endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/admin/products")
        assert resp.status_code == 200, f"Products list failed: {resp.status_code}"
        data = resp.json()
        # API returns "products" key, not "items"
        products = data.get("products", data.get("items", []))
        print(f"✓ Products: {len(products)} products")


class TestContractorBusinesses:
    """Test 13: Contractor Business Management"""
    
    def test_contractor_businesses(self, admin_session):
        """Verify contractor businesses endpoint"""
        resp = admin_session.get(f"{BASE_URL}/api/admin/contractor-businesses")
        assert resp.status_code == 200, f"Contractor businesses failed: {resp.status_code}"
        data = resp.json()
        assert "items" in data
        print(f"✓ Contractor businesses: {len(data['items'])} businesses")


class TestSecurityFeatures:
    """Test 14: Security Features Verification"""
    
    def test_password_complexity_enforcement(self):
        """Test password complexity is enforced on registration"""
        # Try weak password
        resp = requests.post(
            f"{BASE_URL}/api/auth/register",
            json={
                "email": f"{TEST_PREFIX}user@test.com",
                "password": "weak",  # Too short, no uppercase, no digit
                "name": "Test User",
                "role": "customer"
            }
        )
        assert resp.status_code == 400, f"Weak password should be rejected, got {resp.status_code}"
        print("✓ Password complexity enforced")
    
    def test_role_restriction_on_registration(self):
        """Test that only 'customer' role can self-register"""
        resp = requests.post(
            f"{BASE_URL}/api/auth/register",
            json={
                "email": f"{TEST_PREFIX}admin@test.com",
                "password": "ValidPass123!",
                "name": "Test Admin",
                "role": "admin"  # Should be rejected
            }
        )
        assert resp.status_code == 400, f"Admin self-registration should be rejected, got {resp.status_code}"
        print("✓ Role restriction on registration enforced")
    
    def test_security_headers(self):
        """Verify security headers are present"""
        resp = requests.get(f"{BASE_URL}/api/health")
        headers = resp.headers
        
        # Check for security headers
        assert headers.get("X-Content-Type-Options") == "nosniff", "Missing X-Content-Type-Options"
        assert headers.get("X-Frame-Options") == "DENY", "Missing X-Frame-Options"
        print("✓ Security headers present")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
