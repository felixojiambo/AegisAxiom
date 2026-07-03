"""
Contract Templates and SOW Builder Resource Assignment Tests
Tests for:
1. Contract Templates CRUD (Admin section)
2. Template dropdown in SOW Builder resource assignment form
3. Resource assignment with new LEFT/MIDDLE/RIGHT field structure
4. Profit calculations
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin@123!"


class TestContractTemplatesCRUD:
    """Contract Templates CRUD operations - Admin only"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        # Use session token from response
        self.token = data.get("token")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        self.created_template_ids = []
        yield
        # Cleanup created templates
        for template_id in self.created_template_ids:
            try:
                requests.delete(
                    f"{BASE_URL}/api/admin/contract-templates/{template_id}",
                    headers=self.headers
                )
            except:
                pass
    
    def test_get_contract_templates(self):
        """GET /api/admin/contract-templates - List all templates"""
        response = requests.get(
            f"{BASE_URL}/api/admin/contract-templates",
            headers=self.headers
        )
        assert response.status_code == 200, f"Failed to get templates: {response.text}"
        data = response.json()
        assert "items" in data
        assert "total" in data
        print(f"✓ Found {data['total']} contract templates")
    
    def test_create_boh_template(self):
        """POST /api/admin/contract-templates - Create BOH template"""
        payload = {
            "template_name": "TEST_Standard BOH Net-30",
            "description": "Standard Block of Hours with Net-30 payment",
            "contract_type": "boh",
            "default_hours": 200,
            "default_sell_rate": 180.0,
            "billing_terms": "monthly",
            "payment_terms": "net_30",
            "convert_to_boa_at_exhaustion": True,
            "default_contract_duration_days": 90
        }
        
        response = requests.post(
            f"{BASE_URL}/api/admin/contract-templates",
            headers=self.headers,
            json=payload
        )
        assert response.status_code == 200, f"Failed to create template: {response.text}"
        data = response.json()
        
        assert "template" in data
        template = data["template"]
        self.created_template_ids.append(template["template_id"])
        
        # Verify all fields
        assert template["template_name"] == payload["template_name"]
        assert template["contract_type"] == "boh"
        assert template["default_hours"] == 200
        assert template["default_sell_rate"] == 180.0
        assert template["billing_terms"] == "monthly"
        assert template["payment_terms"] == "net_30"
        assert template["convert_to_boa_at_exhaustion"] == True
        assert template["is_active"] == True
        print(f"✓ Created BOH template: {template['template_id']}")
    
    def test_create_fixed_fee_template(self):
        """POST /api/admin/contract-templates - Create Fixed Fee template"""
        payload = {
            "template_name": "TEST_Fixed Fee Professional Services",
            "description": "Fixed fee for professional services projects",
            "contract_type": "fixed_fee",
            "default_fixed_amount": 25000.0,
            "billing_terms": "monthly",
            "payment_terms": "net_45"
        }
        
        response = requests.post(
            f"{BASE_URL}/api/admin/contract-templates",
            headers=self.headers,
            json=payload
        )
        assert response.status_code == 200, f"Failed to create template: {response.text}"
        data = response.json()
        
        template = data["template"]
        self.created_template_ids.append(template["template_id"])
        
        assert template["contract_type"] == "fixed_fee"
        assert template["default_fixed_amount"] == 25000.0
        print(f"✓ Created Fixed Fee template: {template['template_id']}")
    
    def test_create_monthly_template(self):
        """POST /api/admin/contract-templates - Create Monthly Retainer template"""
        payload = {
            "template_name": "TEST_Monthly Retainer Standard",
            "description": "Standard monthly retainer agreement",
            "contract_type": "monthly",
            "default_monthly_rate": 5000.0,
            "default_sell_rate": 150.0,
            "billing_terms": "monthly",
            "payment_terms": "net_30"
        }
        
        response = requests.post(
            f"{BASE_URL}/api/admin/contract-templates",
            headers=self.headers,
            json=payload
        )
        assert response.status_code == 200, f"Failed to create template: {response.text}"
        data = response.json()
        
        template = data["template"]
        self.created_template_ids.append(template["template_id"])
        
        assert template["contract_type"] == "monthly"
        assert template["default_monthly_rate"] == 5000.0
        print(f"✓ Created Monthly Retainer template: {template['template_id']}")
    
    def test_get_single_template(self):
        """GET /api/admin/contract-templates/{id} - Get single template"""
        # First create a template
        payload = {
            "template_name": "TEST_Get Single Template",
            "contract_type": "boa",
            "default_sell_rate": 200.0,
            "billing_terms": "biweekly",
            "payment_terms": "net_14"
        }
        
        create_response = requests.post(
            f"{BASE_URL}/api/admin/contract-templates",
            headers=self.headers,
            json=payload
        )
        assert create_response.status_code == 200
        template_id = create_response.json()["template"]["template_id"]
        self.created_template_ids.append(template_id)
        
        # Get the template
        response = requests.get(
            f"{BASE_URL}/api/admin/contract-templates/{template_id}",
            headers=self.headers
        )
        assert response.status_code == 200, f"Failed to get template: {response.text}"
        template = response.json()
        
        assert template["template_id"] == template_id
        assert template["template_name"] == payload["template_name"]
        assert template["contract_type"] == "boa"
        print(f"✓ Retrieved template: {template_id}")
    
    def test_update_template(self):
        """PUT /api/admin/contract-templates/{id} - Update template"""
        # First create a template
        payload = {
            "template_name": "TEST_Update Template Original",
            "contract_type": "boh",
            "default_hours": 100,
            "default_sell_rate": 150.0,
            "billing_terms": "monthly",
            "payment_terms": "net_30"
        }
        
        create_response = requests.post(
            f"{BASE_URL}/api/admin/contract-templates",
            headers=self.headers,
            json=payload
        )
        assert create_response.status_code == 200
        template_id = create_response.json()["template"]["template_id"]
        self.created_template_ids.append(template_id)
        
        # Update the template
        update_payload = {
            "template_name": "TEST_Update Template Modified",
            "default_hours": 250,
            "default_sell_rate": 200.0,
            "payment_terms": "net_45"
        }
        
        response = requests.put(
            f"{BASE_URL}/api/admin/contract-templates/{template_id}",
            headers=self.headers,
            json=update_payload
        )
        assert response.status_code == 200, f"Failed to update template: {response.text}"
        data = response.json()
        
        template = data["template"]
        assert template["template_name"] == "TEST_Update Template Modified"
        assert template["default_hours"] == 250
        assert template["default_sell_rate"] == 200.0
        assert template["payment_terms"] == "net_45"
        # Original fields should remain
        assert template["contract_type"] == "boh"
        assert template["billing_terms"] == "monthly"
        print(f"✓ Updated template: {template_id}")
    
    def test_delete_template(self):
        """DELETE /api/admin/contract-templates/{id} - Soft delete template"""
        # First create a template
        payload = {
            "template_name": "TEST_Delete Template",
            "contract_type": "boh",
            "default_hours": 50,
            "billing_terms": "weekly",
            "payment_terms": "net_7"
        }
        
        create_response = requests.post(
            f"{BASE_URL}/api/admin/contract-templates",
            headers=self.headers,
            json=payload
        )
        assert create_response.status_code == 200
        template_id = create_response.json()["template"]["template_id"]
        
        # Delete the template
        response = requests.delete(
            f"{BASE_URL}/api/admin/contract-templates/{template_id}",
            headers=self.headers
        )
        assert response.status_code == 200, f"Failed to delete template: {response.text}"
        
        # Verify it's soft deleted (is_active = False)
        get_response = requests.get(
            f"{BASE_URL}/api/admin/contract-templates/{template_id}",
            headers=self.headers
        )
        assert get_response.status_code == 200
        template = get_response.json()
        assert template["is_active"] == False
        print(f"✓ Soft deleted template: {template_id}")
    
    def test_filter_templates_by_contract_type(self):
        """GET /api/admin/contract-templates?contract_type=boh - Filter by type"""
        response = requests.get(
            f"{BASE_URL}/api/admin/contract-templates?contract_type=boh",
            headers=self.headers
        )
        assert response.status_code == 200, f"Failed to filter templates: {response.text}"
        data = response.json()
        
        # All returned templates should be BOH type
        for template in data["items"]:
            assert template["contract_type"] == "boh"
        print(f"✓ Filtered {len(data['items'])} BOH templates")
    
    def test_filter_active_templates(self):
        """GET /api/admin/contract-templates?is_active=true - Filter active only"""
        response = requests.get(
            f"{BASE_URL}/api/admin/contract-templates?is_active=true",
            headers=self.headers
        )
        assert response.status_code == 200, f"Failed to filter templates: {response.text}"
        data = response.json()
        
        # All returned templates should be active
        for template in data["items"]:
            assert template["is_active"] == True
        print(f"✓ Filtered {len(data['items'])} active templates")
    
    def test_duplicate_name_rejected(self):
        """POST /api/admin/contract-templates - Duplicate name should fail"""
        payload = {
            "template_name": "TEST_Duplicate Name Check",
            "contract_type": "boh",
            "billing_terms": "monthly",
            "payment_terms": "net_30"
        }
        
        # Create first template
        response1 = requests.post(
            f"{BASE_URL}/api/admin/contract-templates",
            headers=self.headers,
            json=payload
        )
        assert response1.status_code == 200
        self.created_template_ids.append(response1.json()["template"]["template_id"])
        
        # Try to create duplicate
        response2 = requests.post(
            f"{BASE_URL}/api/admin/contract-templates",
            headers=self.headers,
            json=payload
        )
        assert response2.status_code == 400, "Duplicate name should be rejected"
        assert "already exists" in response2.json().get("detail", "").lower()
        print("✓ Duplicate template name correctly rejected")
    
    def test_invalid_contract_type_rejected(self):
        """POST /api/admin/contract-templates - Invalid contract type should fail"""
        payload = {
            "template_name": "TEST_Invalid Type",
            "contract_type": "invalid_type",
            "billing_terms": "monthly",
            "payment_terms": "net_30"
        }
        
        response = requests.post(
            f"{BASE_URL}/api/admin/contract-templates",
            headers=self.headers,
            json=payload
        )
        assert response.status_code == 400, "Invalid contract type should be rejected"
        print("✓ Invalid contract type correctly rejected")


class TestResourceAssignmentWithTemplates:
    """Resource Assignment with new LEFT/MIDDLE/RIGHT field structure"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        self.token = data.get("token")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        self.created_assignment_ids = []
        yield
        # Cleanup created assignments
        for assignment_id in self.created_assignment_ids:
            try:
                requests.delete(
                    f"{BASE_URL}/api/sales-ops/resource-assignments/{assignment_id}",
                    headers=self.headers
                )
            except:
                pass
    
    def get_test_opportunity(self):
        """Get an opportunity in proposal_development stage for testing"""
        response = requests.get(
            f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=1",
            headers=self.headers
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("items"):
                return data["items"][0]
        return None
    
    def test_create_resource_assignment_with_new_fields(self):
        """POST /api/sales-ops/opportunities/{id}/resource-assignments - Full field structure"""
        opportunity = self.get_test_opportunity()
        if not opportunity:
            pytest.skip("No opportunity in proposal_development stage")
        
        opp_id = opportunity["opportunity_id"]
        
        payload = {
            "resource_type_id": "rt_test_001",
            "resource_type_name": "Senior Developer",
            
            # LEFT side - Customer Billing
            "customer_contract_type": "boh",
            "customer_billing_terms": "monthly",
            "customer_payment_terms": "net_30",
            "customer_hours_purchased": 200,
            "customer_sell_rate": 180.0,
            "convert_to_boa_at_exhaustion": True,
            "total_sell": 36000.0,  # 200 * 180
            
            # RIGHT side - Contractor Cost
            "contractor_business_id": "cb_test_001",
            "contractor_business_name": "Test Contractor Business",
            "contractor_user_id": "user_test_001",
            "contractor_user_name": "Test Contractor",
            "contractor_pay_type": "block_hours",
            "contractor_billing_terms": "weekly",
            "contractor_payment_terms": "net_7",
            "contractor_hours_estimated": 200,
            "contractor_cost_rate": 120.0,
            "total_cost": 24000.0,  # 200 * 120
            
            # MIDDLE - Profit Metrics
            "profit_amount": 12000.0,  # 36000 - 24000
            "profit_margin": 33.33,  # (12000 / 36000) * 100
            
            "notes": "Test resource assignment with full field structure"
        }
        
        response = requests.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/resource-assignments",
            headers=self.headers,
            json=payload
        )
        assert response.status_code == 200, f"Failed to create assignment: {response.text}"
        assignment = response.json()
        
        self.created_assignment_ids.append(assignment["assignment_id"])
        
        # Verify LEFT side fields
        assert assignment["customer_contract_type"] == "boh"
        assert assignment["customer_billing_terms"] == "monthly"
        assert assignment["customer_payment_terms"] == "net_30"
        assert assignment["customer_hours_purchased"] == 200
        assert assignment["customer_sell_rate"] == 180.0
        assert assignment["convert_to_boa_at_exhaustion"] == True
        assert assignment["total_sell"] == 36000.0
        
        # Verify RIGHT side fields
        assert assignment["contractor_business_name"] == "Test Contractor Business"
        assert assignment["contractor_user_name"] == "Test Contractor"
        assert assignment["contractor_pay_type"] == "block_hours"
        assert assignment["contractor_billing_terms"] == "weekly"
        assert assignment["contractor_payment_terms"] == "net_7"
        assert assignment["contractor_cost_rate"] == 120.0
        assert assignment["total_cost"] == 24000.0
        
        # Verify MIDDLE profit metrics
        assert assignment["profit_amount"] == 12000.0
        assert assignment["profit_margin"] == 33.33
        
        print(f"✓ Created resource assignment with full field structure: {assignment['assignment_id']}")
    
    def test_create_fixed_fee_assignment(self):
        """POST - Create fixed fee resource assignment"""
        opportunity = self.get_test_opportunity()
        if not opportunity:
            pytest.skip("No opportunity in proposal_development stage")
        
        opp_id = opportunity["opportunity_id"]
        
        payload = {
            "resource_type_id": "rt_test_002",
            "resource_type_name": "Project Manager",
            
            # LEFT side - Fixed Fee
            "customer_contract_type": "fixed_fee",
            "customer_billing_terms": "monthly",
            "customer_payment_terms": "net_45",
            "customer_fixed_amount": 25000.0,
            "total_sell": 25000.0,
            
            # RIGHT side - Fixed Price
            "contractor_business_id": "cb_test_002",
            "contractor_business_name": "PM Services LLC",
            "contractor_user_id": "user_test_002",
            "contractor_user_name": "Jane PM",
            "contractor_pay_type": "fixed_price",
            "contractor_billing_terms": "monthly",
            "contractor_payment_terms": "net_30",
            "contractor_fixed_cost": 18000.0,
            "total_cost": 18000.0,
            
            # MIDDLE - Profit
            "profit_amount": 7000.0,
            "profit_margin": 28.0
        }
        
        response = requests.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/resource-assignments",
            headers=self.headers,
            json=payload
        )
        assert response.status_code == 200, f"Failed to create assignment: {response.text}"
        assignment = response.json()
        
        self.created_assignment_ids.append(assignment["assignment_id"])
        
        assert assignment["customer_contract_type"] == "fixed_fee"
        assert assignment["customer_fixed_amount"] == 25000.0
        assert assignment["contractor_pay_type"] == "fixed_price"
        assert assignment["contractor_fixed_cost"] == 18000.0
        assert assignment["profit_margin"] == 28.0
        
        print(f"✓ Created fixed fee assignment: {assignment['assignment_id']}")
    
    def test_create_monthly_retainer_assignment(self):
        """POST - Create monthly retainer resource assignment"""
        opportunity = self.get_test_opportunity()
        if not opportunity:
            pytest.skip("No opportunity in proposal_development stage")
        
        opp_id = opportunity["opportunity_id"]
        
        payload = {
            "resource_type_id": "rt_test_003",
            "resource_type_name": "Support Engineer",
            
            # LEFT side - Monthly
            "customer_contract_type": "monthly",
            "customer_billing_terms": "monthly",
            "customer_payment_terms": "net_30",
            "customer_monthly_rate": 8000.0,
            "customer_sell_rate": 150.0,
            "total_sell": 8000.0,
            
            # RIGHT side - Monthly
            "contractor_business_id": "cb_test_003",
            "contractor_business_name": "Support Co",
            "contractor_user_id": "user_test_003",
            "contractor_user_name": "Support Person",
            "contractor_pay_type": "monthly",
            "contractor_billing_terms": "monthly",
            "contractor_payment_terms": "net_14",
            "contractor_monthly_cost": 5500.0,
            "total_cost": 5500.0,
            
            # MIDDLE - Profit
            "profit_amount": 2500.0,
            "profit_margin": 31.25
        }
        
        response = requests.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/resource-assignments",
            headers=self.headers,
            json=payload
        )
        assert response.status_code == 200, f"Failed to create assignment: {response.text}"
        assignment = response.json()
        
        self.created_assignment_ids.append(assignment["assignment_id"])
        
        assert assignment["customer_contract_type"] == "monthly"
        assert assignment["customer_monthly_rate"] == 8000.0
        assert assignment["contractor_pay_type"] == "monthly"
        assert assignment["contractor_monthly_cost"] == 5500.0
        
        print(f"✓ Created monthly retainer assignment: {assignment['assignment_id']}")
    
    def test_get_resource_assignments(self):
        """GET /api/sales-ops/opportunities/{id}/resource-assignments"""
        opportunity = self.get_test_opportunity()
        if not opportunity:
            pytest.skip("No opportunity in proposal_development stage")
        
        opp_id = opportunity["opportunity_id"]
        
        response = requests.get(
            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/resource-assignments",
            headers=self.headers
        )
        assert response.status_code == 200, f"Failed to get assignments: {response.text}"
        data = response.json()
        
        assert "items" in data
        print(f"✓ Retrieved {len(data['items'])} resource assignments for opportunity")
    
    def test_delete_resource_assignment(self):
        """DELETE /api/sales-ops/resource-assignments/{id}"""
        opportunity = self.get_test_opportunity()
        if not opportunity:
            pytest.skip("No opportunity in proposal_development stage")
        
        opp_id = opportunity["opportunity_id"]
        
        # Create an assignment to delete
        payload = {
            "resource_type_id": "rt_delete_test",
            "resource_type_name": "Delete Test Resource",
            "customer_contract_type": "boh",
            "customer_hours_purchased": 10,
            "customer_sell_rate": 100.0,
            "total_sell": 1000.0,
            "contractor_user_id": "user_delete_test",
            "contractor_user_name": "Delete Test User",
            "contractor_pay_type": "block_hours",
            "contractor_cost_rate": 80.0,
            "total_cost": 800.0,
            "profit_amount": 200.0,
            "profit_margin": 20.0
        }
        
        create_response = requests.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/resource-assignments",
            headers=self.headers,
            json=payload
        )
        assert create_response.status_code == 200
        assignment_id = create_response.json()["assignment_id"]
        
        # Delete the assignment
        delete_response = requests.delete(
            f"{BASE_URL}/api/sales-ops/resource-assignments/{assignment_id}",
            headers=self.headers
        )
        assert delete_response.status_code == 200, f"Failed to delete assignment: {delete_response.text}"
        print(f"✓ Deleted resource assignment: {assignment_id}")


class TestContractTemplatesAvailability:
    """Test that templates are available to all authenticated users"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session token"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        self.token = data.get("token")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
    
    def test_templates_available_for_sow_builder(self):
        """Templates should be available for SOW Builder dropdown"""
        response = requests.get(
            f"{BASE_URL}/api/admin/contract-templates?is_active=true",
            headers=self.headers
        )
        assert response.status_code == 200, f"Failed to get templates: {response.text}"
        data = response.json()
        
        # Should have at least some templates
        print(f"✓ {len(data['items'])} active templates available for SOW Builder")
        
        # Verify template structure has all needed fields for form auto-fill
        if data["items"]:
            template = data["items"][0]
            required_fields = [
                "template_id", "template_name", "contract_type",
                "billing_terms", "payment_terms"
            ]
            for field in required_fields:
                assert field in template, f"Missing required field: {field}"
            print("✓ Template structure has all required fields for form auto-fill")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
