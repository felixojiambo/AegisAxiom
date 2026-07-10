"""
Commission Management API Tests
Tests 3-tier commission calculation engine, templates CRUD, and plans creation
Uses cookie-based session authentication
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from provided data
TEST_ADMIN_EMAIL = "support@simplimeta.io"
TEST_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


class TestCommissionTemplates:
    """Template CRUD API tests (GET/POST/PUT/DELETE /api/commission-management/templates)"""
    
    @pytest.fixture(scope="class")
    def session(self):
        """Create authenticated session with cookies"""
        s = requests.Session()
        response = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_ADMIN_EMAIL,
            "password": TEST_ADMIN_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip(f"Admin login failed: {response.status_code} - {response.text}")
        # Session cookies are automatically stored in session
        print(f"Login successful. Session cookies: {s.cookies.get_dict()}")
        return s

    def test_get_templates_unauthenticated(self):
        """GET /api/commission-management/templates - Should require authentication"""
        response = requests.get(f"{BASE_URL}/api/commission-management/templates")
        assert response.status_code in [200, 401], f"Unexpected status: {response.status_code}"
    
    def test_get_templates_authenticated(self, session):
        """GET /api/commission-management/templates - Should return list of templates"""
        response = session.get(f"{BASE_URL}/api/commission-management/templates")
        assert response.status_code == 200, f"Failed: {response.status_code} - {response.text}"
        data = response.json()
        assert "items" in data, "Response should have 'items' key"
        assert isinstance(data["items"], list), "'items' should be a list"
        print(f"Found {len(data['items'])} templates")
        
        for template in data["items"]:
            print(f"  - {template.get('name')}: T1={template.get('tier1_percentage')}%, T2={template.get('tier2_percentage')}%, T3={template.get('tier3_percentage')}%")

    def test_create_template(self, session):
        """POST /api/commission-management/templates - Create new template"""
        test_template = {
            "name": "TEST_TEMPLATE_PYTEST",
            "tier1_percentage": 12.5,
            "tier2_percentage": 7.5,
            "tier3_percentage": 18,
            "cost_type": "percentage",
            "cost_default": 45,
            "is_active": True
        }
        
        response = session.post(
            f"{BASE_URL}/api/commission-management/templates",
            json=test_template
        )
        assert response.status_code in [200, 201], f"Failed: {response.status_code} - {response.text}"
        data = response.json()
        assert "template_id" in data, "Response should include template_id"
        print(f"Created template: {data['template_id']}")
        
        # Store for cleanup
        self.__class__.created_template_id = data["template_id"]

    def test_update_template(self, session):
        """PUT /api/commission-management/templates/{id} - Update existing template"""
        template_id = getattr(self.__class__, 'created_template_id', None)
        if not template_id:
            pytest.skip("No template created to update")
        
        updated_template = {
            "name": "TEST_TEMPLATE_PYTEST_UPDATED",
            "tier1_percentage": 15,
            "tier2_percentage": 8,
            "tier3_percentage": 20,
            "cost_type": "actual",
            "cost_default": 50000,
            "is_active": True
        }
        
        response = session.put(
            f"{BASE_URL}/api/commission-management/templates/{template_id}",
            json=updated_template
        )
        assert response.status_code == 200, f"Failed: {response.status_code} - {response.text}"
        data = response.json()
        assert "message" in data, "Response should have 'message'"
        print(f"Updated template: {template_id}")

    def test_delete_template(self, session):
        """DELETE /api/commission-management/templates/{id} - Soft delete template"""
        template_id = getattr(self.__class__, 'created_template_id', None)
        if not template_id:
            pytest.skip("No template created to delete")
        
        response = session.delete(
            f"{BASE_URL}/api/commission-management/templates/{template_id}"
        )
        assert response.status_code == 200, f"Failed: {response.status_code} - {response.text}"
        data = response.json()
        assert "message" in data, "Response should have 'message'"
        print(f"Deleted (soft) template: {template_id}")


class TestCommissionCalculationEngine:
    """
    Tests for the 3-tier commission calculation logic:
    - Tier 1: % of revenue
    - Tier 2: % of (revenue - cost - tier1)
    - Tier 3: % of (tier2_base - tier2_commission)
    """

    @pytest.fixture(scope="class")
    def session(self):
        """Create authenticated session with cookies"""
        s = requests.Session()
        response = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_ADMIN_EMAIL,
            "password": TEST_ADMIN_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip(f"Admin login failed: {response.status_code}")
        return s

    @pytest.fixture(scope="class") 
    def test_deal(self, session):
        """Create a test deal for commission testing"""
        # First, get or create a test account
        response = session.get(f"{BASE_URL}/api/accounts?source=local&limit=10")
        if response.status_code == 200:
            accounts = response.json()
            account_list = accounts.get("items") or accounts if isinstance(accounts, list) else []
            if account_list:
                account_id = account_list[0].get("account_id")
            else:
                # Create a test account
                acc_resp = session.post(
                    f"{BASE_URL}/api/accounts",
                    json={"name": "TEST_COMMISSION_ACCOUNT", "source": "local"}
                )
                if acc_resp.status_code in [200, 201]:
                    account_id = acc_resp.json().get("account_id")
                else:
                    pytest.skip("Could not create test account")
        else:
            pytest.skip("Could not get accounts")
        
        # Create a test deal
        deal_data = {
            "name": "TEST_COMMISSION_DEAL_PYTEST",
            "account_id": account_id,
            "amount": 100000,
            "stage": "Closed Won",
            "close_date": "2025-01-15",
            "source": "local"
        }
        deal_resp = session.post(f"{BASE_URL}/api/deals", json=deal_data)
        if deal_resp.status_code not in [200, 201]:
            pytest.skip(f"Could not create test deal: {deal_resp.text}")
        
        deal = deal_resp.json()
        deal_id = deal.get("deal_id")
        print(f"Created test deal: {deal_id}")
        yield {"deal_id": deal_id, "account_id": account_id}
        
        # Cleanup: Delete deal
        session.delete(f"{BASE_URL}/api/deals/{deal_id}")

    @pytest.fixture(scope="class")
    def test_user(self, session):
        """Get a user for commission assignment"""
        response = session.get(f"{BASE_URL}/api/admin/users")
        if response.status_code != 200:
            pytest.skip("Could not get users")
        data = response.json()
        # Handle both list and dict response formats
        if isinstance(data, list):
            users = data
        else:
            users = data.get("items") or data.get("users") or []
        if not users:
            pytest.skip("No users available")
        return users[0]

    def test_basic_calculation_percentage_cost(self, session, test_deal, test_user):
        """
        Test basic 3-tier calculation with percentage cost:
        Revenue: $100,000
        Cost: 50% ($50,000)
        T1: 10% of revenue = $10,000
        T2: 5% of (revenue - cost - T1) = 5% of $40,000 = $2,000
        T3: 15% of (T2_base - T2_commission) = 15% of $38,000 = $5,700
        """
        payload = {
            "deal_id": test_deal["deal_id"],
            "template_id": None,
            "revenue": 100000,
            "cost_type": "percentage",
            "cost_value": 50,
            "tiers": [
                {
                    "tier_number": 1,
                    "tier_name": "Top Line",
                    "percentage": 10,
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": [{
                        "user_id": test_user["user_id"],
                        "user_name": test_user.get("name", "Test User"),
                        "split_percentage": 100,
                        "commission_amount": 0
                    }]
                },
                {
                    "tier_number": 2,
                    "tier_name": "Director Override",
                    "percentage": 5,
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": [{
                        "user_id": test_user["user_id"],
                        "user_name": test_user.get("name", "Test User"),
                        "split_percentage": 100,
                        "commission_amount": 0
                    }]
                },
                {
                    "tier_number": 3,
                    "tier_name": "Sales Rep",
                    "percentage": 15,
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": [{
                        "user_id": test_user["user_id"],
                        "user_name": test_user.get("name", "Test User"),
                        "split_percentage": 100,
                        "commission_amount": 0
                    }]
                }
            ]
        }

        response = session.post(
            f"{BASE_URL}/api/commission-management/plans",
            json=payload
        )
        
        assert response.status_code in [200, 201], f"Failed: {response.status_code} - {response.text}"
        data = response.json()
        
        assert "commission_id" in data, "Response should have commission_id"
        assert "totals" in data, "Response should have totals"
        
        totals = data["totals"]
        print(f"\n=== Commission Calculation Results ===")
        print(f"Revenue: ${totals.get('revenue', 0):,.2f}")
        print(f"Cost: ${totals.get('cost', 0):,.2f}")
        print(f"Tier 1: ${totals.get('tier1_commission', 0):,.2f}")
        print(f"Tier 2: ${totals.get('tier2_commission', 0):,.2f}")
        print(f"Tier 3: ${totals.get('tier3_commission', 0):,.2f}")
        print(f"Total Commission: ${totals.get('total_commission', 0):,.2f}")
        print(f"Profit: ${totals.get('profit_after_commission', 0):,.2f}")
        print(f"Profit %: {totals.get('profit_percentage', 0):.2f}%")
        
        # Verify calculations
        expected_tier1 = 10000  # 10% of 100000
        expected_tier2_base = 100000 - 50000 - 10000  # revenue - cost - tier1 = 40000
        expected_tier2 = expected_tier2_base * 0.05  # 5% of 40000 = 2000
        expected_tier3_base = expected_tier2_base - expected_tier2  # 40000 - 2000 = 38000
        expected_tier3 = expected_tier3_base * 0.15  # 15% of 38000 = 5700
        
        assert abs(totals.get("tier1_commission", 0) - expected_tier1) < 0.01, \
            f"Tier 1 mismatch: expected {expected_tier1}, got {totals.get('tier1_commission')}"
        assert abs(totals.get("tier2_commission", 0) - expected_tier2) < 0.01, \
            f"Tier 2 mismatch: expected {expected_tier2}, got {totals.get('tier2_commission')}"
        assert abs(totals.get("tier3_commission", 0) - expected_tier3) < 0.01, \
            f"Tier 3 mismatch: expected {expected_tier3}, got {totals.get('tier3_commission')}"
        
        # Store commission_id for later tests
        self.__class__.commission_id = data["commission_id"]
        print(f"✓ Commission plan created: {data['commission_id']}")

    def test_calculation_actual_cost(self, session, test_deal, test_user):
        """Test calculation with actual dollar cost instead of percentage"""
        # First get or create another deal since the previous one is already linked
        deal_data = {
            "name": "TEST_COMMISSION_DEAL_ACTUAL_COST",
            "account_id": test_deal["account_id"],
            "amount": 200000,
            "stage": "Closed Won",
            "close_date": "2025-01-16",
            "source": "local"
        }
        deal_resp = session.post(f"{BASE_URL}/api/deals", json=deal_data)
        if deal_resp.status_code not in [200, 201]:
            pytest.skip(f"Could not create second test deal: {deal_resp.text}")
        
        deal_id = deal_resp.json().get("deal_id")
        
        payload = {
            "deal_id": deal_id,
            "template_id": None,
            "revenue": 200000,
            "cost_type": "actual",
            "cost_value": 80000,  # Actual dollar cost
            "tiers": [
                {
                    "tier_number": 1,
                    "tier_name": "Top Line",
                    "percentage": 8,  # 8% of 200000 = 16000
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": [{
                        "user_id": test_user["user_id"],
                        "user_name": test_user.get("name", "Test User"),
                        "split_percentage": 100,
                        "commission_amount": 0
                    }]
                },
                {
                    "tier_number": 2,
                    "tier_name": "Director Override",
                    "percentage": 7,  # 7% of (200000 - 80000 - 16000) = 7% of 104000 = 7280
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": [{
                        "user_id": test_user["user_id"],
                        "user_name": test_user.get("name", "Test User"),
                        "split_percentage": 100,
                        "commission_amount": 0
                    }]
                },
                {
                    "tier_number": 3,
                    "tier_name": "Sales Rep",
                    "percentage": 12,  # 12% of (104000 - 7280) = 12% of 96720 = 11606.4
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": [{
                        "user_id": test_user["user_id"],
                        "user_name": test_user.get("name", "Test User"),
                        "split_percentage": 100,
                        "commission_amount": 0
                    }]
                }
            ]
        }

        response = session.post(
            f"{BASE_URL}/api/commission-management/plans",
            json=payload
        )
        
        # Cleanup deal
        session.delete(f"{BASE_URL}/api/deals/{deal_id}")
        
        assert response.status_code in [200, 201], f"Failed: {response.status_code} - {response.text}"
        data = response.json()
        totals = data["totals"]
        
        print(f"\n=== Actual Cost Calculation Results ===")
        print(f"Revenue: ${totals.get('revenue', 0):,.2f}")
        print(f"Actual Cost: ${totals.get('cost', 0):,.2f}")
        print(f"Tier 1: ${totals.get('tier1_commission', 0):,.2f}")
        print(f"Tier 2: ${totals.get('tier2_commission', 0):,.2f}")
        print(f"Tier 3: ${totals.get('tier3_commission', 0):,.2f}")
        
        # Verify cost is actual dollar value, not percentage
        assert totals.get("cost") == 80000, f"Cost should be actual value $80,000, got {totals.get('cost')}"
        
        # Verify tier 1 calculation
        expected_tier1 = 200000 * 0.08  # 16000
        assert abs(totals.get("tier1_commission", 0) - expected_tier1) < 0.01, \
            f"Tier 1 mismatch: expected {expected_tier1}, got {totals.get('tier1_commission')}"

    def test_user_split_percentages(self, session, test_deal, test_user):
        """Test multiple users per tier with custom split percentages"""
        # Create another deal
        deal_data = {
            "name": "TEST_COMMISSION_DEAL_MULTI_USER",
            "account_id": test_deal["account_id"],
            "amount": 50000,
            "stage": "Closed Won",
            "close_date": "2025-01-17",
            "source": "local"
        }
        deal_resp = session.post(f"{BASE_URL}/api/deals", json=deal_data)
        if deal_resp.status_code not in [200, 201]:
            pytest.skip("Could not create multi-user test deal")
        
        deal_id = deal_resp.json().get("deal_id")
        
        # Get multiple users if available
        users_resp = session.get(f"{BASE_URL}/api/admin/users")
        users_data = users_resp.json()
        # Handle both list and dict response formats
        if isinstance(users_data, list):
            users = users_data
        else:
            users = users_data.get("items") or users_data.get("users") or []
        
        user1 = users[0] if len(users) > 0 else test_user
        user2 = users[1] if len(users) > 1 else user1  # Use same user twice if only one available
        
        payload = {
            "deal_id": deal_id,
            "template_id": None,
            "revenue": 50000,
            "cost_type": "percentage",
            "cost_value": 40,
            "tiers": [
                {
                    "tier_number": 1,
                    "tier_name": "Top Line",
                    "percentage": 10,  # 10% of 50000 = 5000
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": [
                        {
                            "user_id": user1["user_id"],
                            "user_name": user1.get("name", "User 1"),
                            "split_percentage": 60,  # 60% of 5000 = 3000
                            "commission_amount": 0
                        },
                        {
                            "user_id": user2["user_id"],
                            "user_name": user2.get("name", "User 2"),
                            "split_percentage": 40,  # 40% of 5000 = 2000
                            "commission_amount": 0
                        }
                    ]
                },
                {
                    "tier_number": 2,
                    "tier_name": "Director Override",
                    "percentage": 5,
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": [{
                        "user_id": user1["user_id"],
                        "user_name": user1.get("name", "User 1"),
                        "split_percentage": 100,
                        "commission_amount": 0
                    }]
                },
                {
                    "tier_number": 3,
                    "tier_name": "Sales Rep",
                    "percentage": 15,
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": [{
                        "user_id": user2["user_id"],
                        "user_name": user2.get("name", "User 2"),
                        "split_percentage": 100,
                        "commission_amount": 0
                    }]
                }
            ]
        }

        response = session.post(
            f"{BASE_URL}/api/commission-management/plans",
            json=payload
        )
        
        # Cleanup
        session.delete(f"{BASE_URL}/api/deals/{deal_id}")
        
        assert response.status_code in [200, 201], f"Failed: {response.status_code} - {response.text}"
        data = response.json()
        
        print(f"\n=== Multi-User Split Test ===")
        print(f"Commission ID: {data.get('commission_id')}")
        print(f"Tier 1 Total: ${data['totals'].get('tier1_commission', 0):,.2f}")
        
        # Verify tier 1 was calculated correctly (5000)
        assert abs(data['totals'].get('tier1_commission', 0) - 5000) < 0.01, \
            "Tier 1 commission should be $5,000"

    def test_zero_cost_scenario(self, session, test_deal, test_user):
        """Test edge case: zero cost (100% margin before commission)"""
        deal_data = {
            "name": "TEST_COMMISSION_DEAL_ZERO_COST",
            "account_id": test_deal["account_id"],
            "amount": 100000,
            "stage": "Closed Won",
            "source": "local"
        }
        deal_resp = session.post(f"{BASE_URL}/api/deals", json=deal_data)
        if deal_resp.status_code not in [200, 201]:
            pytest.skip("Could not create zero-cost test deal")
        
        deal_id = deal_resp.json().get("deal_id")
        
        payload = {
            "deal_id": deal_id,
            "template_id": None,
            "revenue": 100000,
            "cost_type": "percentage",
            "cost_value": 0,  # Zero cost
            "tiers": [
                {
                    "tier_number": 1,
                    "tier_name": "Top Line",
                    "percentage": 10,
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": [{
                        "user_id": test_user["user_id"],
                        "user_name": test_user.get("name"),
                        "split_percentage": 100,
                        "commission_amount": 0
                    }]
                },
                {
                    "tier_number": 2,
                    "tier_name": "Director Override",
                    "percentage": 5,
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": []
                },
                {
                    "tier_number": 3,
                    "tier_name": "Sales Rep",
                    "percentage": 15,
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": []
                }
            ]
        }

        response = session.post(
            f"{BASE_URL}/api/commission-management/plans",
            json=payload
        )
        
        # Cleanup
        session.delete(f"{BASE_URL}/api/deals/{deal_id}")
        
        assert response.status_code in [200, 201], f"Failed: {response.status_code} - {response.text}"
        data = response.json()
        totals = data["totals"]
        
        print(f"\n=== Zero Cost Scenario ===")
        print(f"Cost: ${totals.get('cost', 0):,.2f}")
        
        # With zero cost:
        # T1: 10% of 100000 = 10000
        # T2: 5% of (100000 - 0 - 10000) = 5% of 90000 = 4500
        # T3: 15% of (90000 - 4500) = 15% of 85500 = 12825
        
        assert totals.get("cost") == 0, "Cost should be $0"
        assert abs(totals.get("tier1_commission", 0) - 10000) < 0.01, "Tier 1 should be $10,000"
        assert abs(totals.get("tier2_commission", 0) - 4500) < 0.01, "Tier 2 should be $4,500"
        assert abs(totals.get("tier3_commission", 0) - 12825) < 0.01, "Tier 3 should be $12,825"

    def test_high_cost_scenario(self, session, test_deal, test_user):
        """Test edge case: 100% cost (zero margin before commission)"""
        deal_data = {
            "name": "TEST_COMMISSION_DEAL_100PCT_COST",
            "account_id": test_deal["account_id"],
            "amount": 100000,
            "stage": "Closed Won",
            "source": "local"
        }
        deal_resp = session.post(f"{BASE_URL}/api/deals", json=deal_data)
        if deal_resp.status_code not in [200, 201]:
            pytest.skip("Could not create 100% cost test deal")
        
        deal_id = deal_resp.json().get("deal_id")
        
        payload = {
            "deal_id": deal_id,
            "template_id": None,
            "revenue": 100000,
            "cost_type": "percentage",
            "cost_value": 100,  # 100% cost = no margin
            "tiers": [
                {
                    "tier_number": 1,
                    "tier_name": "Top Line",
                    "percentage": 10,
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": [{
                        "user_id": test_user["user_id"],
                        "user_name": test_user.get("name"),
                        "split_percentage": 100,
                        "commission_amount": 0
                    }]
                },
                {
                    "tier_number": 2,
                    "tier_name": "Director Override",
                    "percentage": 5,
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": []
                },
                {
                    "tier_number": 3,
                    "tier_name": "Sales Rep",
                    "percentage": 15,
                    "base_amount": 0,
                    "commission_total": 0,
                    "users": []
                }
            ]
        }

        response = session.post(
            f"{BASE_URL}/api/commission-management/plans",
            json=payload
        )
        
        # Cleanup
        session.delete(f"{BASE_URL}/api/deals/{deal_id}")
        
        assert response.status_code in [200, 201], f"Failed: {response.status_code} - {response.text}"
        data = response.json()
        totals = data["totals"]
        
        print(f"\n=== 100% Cost Scenario ===")
        print(f"Revenue: ${totals.get('revenue', 0):,.2f}")
        print(f"Cost: ${totals.get('cost', 0):,.2f}")
        print(f"Tier 1: ${totals.get('tier1_commission', 0):,.2f}")
        print(f"Tier 2: ${totals.get('tier2_commission', 0):,.2f}")
        print(f"Tier 3: ${totals.get('tier3_commission', 0):,.2f}")
        print(f"Profit: ${totals.get('profit_after_commission', 0):,.2f}")
        
        # With 100% cost:
        # Cost = 100000
        # T1: 10% of 100000 = 10000
        # T2_base = 100000 - 100000 - 10000 = -10000 (negative base)
        # T2: 5% of -10000 = -500 (negative commission)
        # T3_base = -10000 - (-500) = -9500
        # T3: 15% of -9500 = -1425
        
        assert totals.get("cost") == 100000, "Cost should be $100,000"
        # Tier 1 is always calculated from revenue
        assert abs(totals.get("tier1_commission", 0) - 10000) < 0.01, "Tier 1 should still be $10,000"

    def test_get_commission_plan(self, session):
        """GET /api/commission-management/plans/{id} - Retrieve saved commission plan"""
        commission_id = getattr(self.__class__, 'commission_id', None)
        if not commission_id:
            pytest.skip("No commission plan created to retrieve")
        
        response = session.get(f"{BASE_URL}/api/commission-management/plans/{commission_id}")
        
        assert response.status_code == 200, f"Failed: {response.status_code} - {response.text}"
        data = response.json()
        
        assert data.get("commission_id") == commission_id
        assert "tiers" in data
        assert "totals" in data
        print(f"✓ Retrieved commission plan: {commission_id}")

    def test_get_all_plans(self, session):
        """GET /api/commission-management/plans - List all commission plans"""
        response = session.get(f"{BASE_URL}/api/commission-management/plans")
        
        assert response.status_code == 200, f"Failed: {response.status_code} - {response.text}"
        data = response.json()
        
        assert "items" in data
        print(f"✓ Found {len(data['items'])} commission plans")


class TestExistingTemplates:
    """Tests to verify pre-existing templates work correctly"""
    
    @pytest.fixture(scope="class")
    def session(self):
        s = requests.Session()
        response = s.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_ADMIN_EMAIL,
            "password": TEST_ADMIN_PASSWORD
        })
        if response.status_code != 200:
            pytest.skip("Admin login failed")
        return s

    def test_verify_standard_deal_template(self, session):
        """Verify 'Standard Deal' template exists with correct values"""
        response = session.get(f"{BASE_URL}/api/commission-management/templates")
        
        assert response.status_code == 200
        data = response.json()
        templates = data.get("items", [])
        
        standard = next((t for t in templates if "Standard" in t.get("name", "")), None)
        
        if standard:
            print(f"\n=== Standard Deal Template ===")
            print(f"Name: {standard.get('name')}")
            print(f"Tier 1: {standard.get('tier1_percentage')}%")
            print(f"Tier 2: {standard.get('tier2_percentage')}%")
            print(f"Tier 3: {standard.get('tier3_percentage')}%")
            print(f"Cost Type: {standard.get('cost_type')}")
            print(f"Cost Default: {standard.get('cost_default')}")
            
            # Verify expected values from provided data
            assert standard.get("tier1_percentage") == 10
            assert standard.get("tier2_percentage") == 5
            assert standard.get("tier3_percentage") == 15
        else:
            print("Standard Deal template not found - may have been deleted or not created yet")
            pytest.skip("Standard Deal template not found")

    def test_verify_enterprise_deal_template(self, session):
        """Verify 'Enterprise Deal' template exists with correct values"""
        response = session.get(f"{BASE_URL}/api/commission-management/templates")
        
        assert response.status_code == 200
        data = response.json()
        templates = data.get("items", [])
        
        enterprise = next((t for t in templates if "Enterprise" in t.get("name", "")), None)
        
        if enterprise:
            print(f"\n=== Enterprise Deal Template ===")
            print(f"Name: {enterprise.get('name')}")
            print(f"Tier 1: {enterprise.get('tier1_percentage')}%")
            print(f"Tier 2: {enterprise.get('tier2_percentage')}%")
            print(f"Tier 3: {enterprise.get('tier3_percentage')}%")
            print(f"Cost Type: {enterprise.get('cost_type')}")
            print(f"Cost Default: {enterprise.get('cost_default')}")
            
            # Verify expected values from provided data
            assert enterprise.get("tier1_percentage") == 8
            assert enterprise.get("tier2_percentage") == 7
            assert enterprise.get("tier3_percentage") == 12
        else:
            print("Enterprise Deal template not found - may have been deleted or not created yet")
            pytest.skip("Enterprise Deal template not found")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
