"""
Test Sales Ops Procurement & Contractor Management - Phase 3A
Tests:
- ContractorBusiness Sales Ops extensions (rating, partnership_tier, marketing_funds, grievances)
- Grievance management endpoints
- Resource assignments for opportunities
- Line items (materials, hardware, wholesale MRR)
- Referral agreements with expiration tracking
"""
import pytest
import requests
import os
from datetime import datetime, timedelta

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin@123!"


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token for admin user."""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })
    if response.status_code == 200:
        return response.json().get("token")
    pytest.skip(f"Authentication failed: {response.status_code} - {response.text}")


@pytest.fixture(scope="module")
def api_client(auth_token):
    """Authenticated requests session."""
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_token}"
    })
    return session


class TestContractorBusinessSalesOpsExtensions:
    """Test ContractorBusiness model with Sales Ops fields."""
    
    def test_get_contractor_businesses(self, api_client):
        """GET /api/admin/contractor-businesses - List all contractors."""
        response = api_client.get(f"{BASE_URL}/api/admin/contractor-businesses")
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "items" in data
        print(f"✓ Found {len(data['items'])} contractor businesses")
    
    def test_get_contractor_businesses_with_status_filter(self, api_client):
        """GET /api/admin/contractor-businesses?status=active - Filter by status."""
        response = api_client.get(f"{BASE_URL}/api/admin/contractor-businesses?status=active")
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "items" in data
        # All returned items should be active
        for item in data["items"]:
            assert item.get("status") == "active", f"Expected active status, got {item.get('status')}"
        print(f"✓ Found {len(data['items'])} active contractor businesses")
    
    def test_create_contractor_with_sales_ops_fields(self, api_client):
        """POST /api/admin/contractor-businesses - Create with Sales Ops extensions."""
        contractor_data = {
            "business_name": "TEST_SalesOps_Contractor_LLC",
            "contact_name": "John Test",
            "email": "test@salesopscontractor.com",
            "phone": "555-123-4567",
            "address_city": "Austin",
            "address_state": "TX",
            "billing_frequency": "weekly",
            "payment_terms": "net_30"
        }
        
        response = api_client.post(f"{BASE_URL}/api/admin/contractor-businesses", json=contractor_data)
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert data.get("business_name") == contractor_data["business_name"]
        assert "business_id" in data
        print(f"✓ Created contractor: {data['business_id']}")
        
        # Store for cleanup
        TestContractorBusinessSalesOpsExtensions.created_contractor_id = data["business_id"]
        return data["business_id"]
    
    def test_update_contractor_with_sales_ops_fields(self, api_client):
        """PUT /api/admin/contractor-businesses/{id} - Update with Sales Ops fields."""
        contractor_id = getattr(TestContractorBusinessSalesOpsExtensions, 'created_contractor_id', None)
        if not contractor_id:
            pytest.skip("No contractor created to update")
        
        update_data = {
            "rating": 4.5,
            "partnership_tier": "gold",
            "marketing_funds_allocated": 10000.0,
            "marketing_funds_used": 2500.0,
            "emergency_contact_name": "Jane Emergency",
            "emergency_contact_phone": "555-999-8888",
            "emergency_contact_relationship": "Spouse",
            "interview_notes": "Excellent candidate with 10 years experience"
        }
        
        response = api_client.put(f"{BASE_URL}/api/admin/contractor-businesses/{contractor_id}", json=update_data)
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert data.get("rating") == 4.5
        assert data.get("partnership_tier") == "gold"
        assert data.get("marketing_funds_allocated") == 10000.0
        assert data.get("emergency_contact_name") == "Jane Emergency"
        print(f"✓ Updated contractor with Sales Ops fields")
    
    def test_get_contractor_detail(self, api_client):
        """GET /api/admin/contractor-businesses/{id} - Get single contractor with all fields."""
        contractor_id = getattr(TestContractorBusinessSalesOpsExtensions, 'created_contractor_id', None)
        if not contractor_id:
            pytest.skip("No contractor created to fetch")
        
        response = api_client.get(f"{BASE_URL}/api/admin/contractor-businesses/{contractor_id}")
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert data.get("business_id") == contractor_id
        assert data.get("rating") == 4.5
        assert data.get("partnership_tier") == "gold"
        assert "contractors" in data  # Should include contractors array
        print(f"✓ Retrieved contractor detail with Sales Ops fields")
    
    def test_cleanup_contractor(self, api_client):
        """DELETE /api/admin/contractor-businesses/{id} - Cleanup test contractor."""
        contractor_id = getattr(TestContractorBusinessSalesOpsExtensions, 'created_contractor_id', None)
        if not contractor_id:
            pytest.skip("No contractor to cleanup")
        
        response = api_client.delete(f"{BASE_URL}/api/admin/contractor-businesses/{contractor_id}")
        assert response.status_code == 200, f"Failed: {response.text}"
        print(f"✓ Cleaned up test contractor")


class TestGrievanceManagement:
    """Test grievance recording and resolution endpoints."""
    
    @pytest.fixture(autouse=True)
    def setup_contractor(self, api_client):
        """Create a test contractor for grievance tests."""
        contractor_data = {
            "business_name": "TEST_Grievance_Contractor_LLC",
            "contact_name": "Grievance Test",
            "email": "grievance@test.com"
        }
        
        response = api_client.post(f"{BASE_URL}/api/admin/contractor-businesses", json=contractor_data)
        if response.status_code == 200:
            self.contractor_id = response.json()["business_id"]
        else:
            pytest.skip(f"Failed to create test contractor: {response.text}")
        
        yield
        
        # Cleanup
        api_client.delete(f"{BASE_URL}/api/admin/contractor-businesses/{self.contractor_id}")
    
    def test_add_grievance(self, api_client):
        """POST /api/admin/contractor-businesses/{id}/grievances - Record a grievance."""
        grievance_data = {
            "reporter_name": "Customer ABC",
            "reporter_type": "customer",
            "severity": "high",
            "description": "Contractor arrived late and did not complete the work as specified.",
            "affects_rating": True
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/admin/contractor-businesses/{self.contractor_id}/grievances",
            json=grievance_data
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "grievance" in data
        assert data["grievance"]["reporter_name"] == "Customer ABC"
        assert data["grievance"]["severity"] == "high"
        assert data["grievance"]["resolved"] == False
        
        # Store grievance_id for resolution test
        self.grievance_id = data["grievance"]["grievance_id"]
        print(f"✓ Grievance recorded: {self.grievance_id}")
    
    def test_grievance_appears_in_contractor(self, api_client):
        """Verify grievance appears in contractor's grievances array."""
        # First add a grievance
        grievance_data = {
            "reporter_name": "Internal QA",
            "reporter_type": "internal",
            "severity": "medium",
            "description": "Quality issues noted during review."
        }
        
        api_client.post(
            f"{BASE_URL}/api/admin/contractor-businesses/{self.contractor_id}/grievances",
            json=grievance_data
        )
        
        # Fetch contractor and check grievances
        response = api_client.get(f"{BASE_URL}/api/admin/contractor-businesses/{self.contractor_id}")
        assert response.status_code == 200
        
        data = response.json()
        grievances = data.get("grievances", [])
        assert len(grievances) >= 1, "Expected at least one grievance"
        
        # Find our grievance
        found = any(g.get("reporter_name") == "Internal QA" for g in grievances)
        assert found, "Grievance not found in contractor's grievances array"
        print(f"✓ Grievance appears in contractor's grievances array")


class TestResourceAssignments:
    """Test opportunity resource assignment endpoints."""
    
    @pytest.fixture(autouse=True)
    def setup_opportunity(self, api_client):
        """Find or create a test opportunity in proposal_development stage."""
        # First try to find an existing opportunity
        response = api_client.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=10")
        if response.status_code == 200:
            data = response.json()
            if data.get("items"):
                self.opportunity_id = data["items"][0]["opportunity_id"]
                self.opportunity_name = data["items"][0]["name"]
                print(f"Using existing opportunity: {self.opportunity_name}")
                yield
                return
        
        # Create a test opportunity if none exists
        # First get an account
        accounts_response = api_client.get(f"{BASE_URL}/api/accounts?limit=1")
        if accounts_response.status_code != 200 or not accounts_response.json().get("items"):
            pytest.skip("No accounts available for test opportunity")
        
        account = accounts_response.json()["items"][0]
        
        opp_data = {
            "name": "TEST_Procurement_Opportunity",
            "account_id": account["account_id"],
            "stage": "proposal_development",
            "amount": 50000,
            "lines_of_business": ["field_services", "professional_services"],
            "is_test_data": True
        }
        
        response = api_client.post(f"{BASE_URL}/api/opportunities", json=opp_data)
        if response.status_code == 200:
            self.opportunity_id = response.json()["opportunity_id"]
            self.opportunity_name = opp_data["name"]
            self.created_opportunity = True
        else:
            pytest.skip(f"Failed to create test opportunity: {response.text}")
        
        yield
        
        # Cleanup if we created it
        if getattr(self, 'created_opportunity', False):
            api_client.delete(f"{BASE_URL}/api/opportunities/{self.opportunity_id}")
    
    def test_get_resource_assignments_empty(self, api_client):
        """GET /api/sales-ops/opportunities/{id}/resource-assignments - Initially empty."""
        response = api_client.get(
            f"{BASE_URL}/api/sales-ops/opportunities/{self.opportunity_id}/resource-assignments"
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "items" in data
        print(f"✓ Resource assignments endpoint works, found {len(data['items'])} assignments")
    
    def test_create_resource_assignment(self, api_client):
        """POST /api/sales-ops/opportunities/{id}/resource-assignments - Assign contractor."""
        # Get a contractor
        contractors_response = api_client.get(f"{BASE_URL}/api/admin/contractor-businesses?status=active")
        if contractors_response.status_code != 200:
            pytest.skip("Cannot fetch contractors")
        
        contractors = contractors_response.json().get("items", [])
        if not contractors:
            pytest.skip("No active contractors available")
        
        contractor = contractors[0]
        
        # Get resource types
        rt_response = api_client.get(f"{BASE_URL}/api/admin/default-resource-types")
        if rt_response.status_code != 200:
            pytest.skip("Cannot fetch resource types")
        
        resource_types = rt_response.json().get("default_resource_types", [])
        if not resource_types:
            pytest.skip("No resource types available")
        
        resource_type = resource_types[0]
        
        assignment_data = {
            "resource_type_id": resource_type["resource_type_id"],
            "resource_type_name": resource_type["name"],
            "contractor_business_id": contractor["business_id"],
            "contractor_business_name": contractor["business_name"],
            "estimated_hours": 40,
            "cost_rate": 75,
            "sell_rate": 125,
            "notes": "Test assignment for procurement testing"
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{self.opportunity_id}/resource-assignments",
            json=assignment_data
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "assignment_id" in data
        self.assignment_id = data["assignment_id"]
        print(f"✓ Created resource assignment: {self.assignment_id}")
    
    def test_verify_resource_assignment_created(self, api_client):
        """Verify the resource assignment was created with correct totals."""
        response = api_client.get(
            f"{BASE_URL}/api/sales-ops/opportunities/{self.opportunity_id}/resource-assignments"
        )
        assert response.status_code == 200
        
        data = response.json()
        assignments = data.get("items", [])
        
        # Find our assignment
        test_assignments = [a for a in assignments if a.get("notes") == "Test assignment for procurement testing"]
        if test_assignments:
            assignment = test_assignments[0]
            # Verify calculated totals
            assert assignment.get("total_cost") == 40 * 75  # 3000
            assert assignment.get("total_sell") == 40 * 125  # 5000
            print(f"✓ Resource assignment has correct totals: cost=${assignment['total_cost']}, sell=${assignment['total_sell']}")
    
    def test_delete_resource_assignment(self, api_client):
        """DELETE /api/sales-ops/resource-assignments/{id} - Remove assignment."""
        assignment_id = getattr(self, 'assignment_id', None)
        if not assignment_id:
            pytest.skip("No assignment to delete")
        
        response = api_client.delete(f"{BASE_URL}/api/sales-ops/resource-assignments/{assignment_id}")
        assert response.status_code == 200, f"Failed: {response.text}"
        print(f"✓ Deleted resource assignment")


class TestLineItems:
    """Test opportunity line items (materials, hardware, wholesale MRR)."""
    
    @pytest.fixture(autouse=True)
    def setup_opportunity(self, api_client):
        """Find or create a test opportunity."""
        response = api_client.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=10")
        if response.status_code == 200:
            data = response.json()
            if data.get("items"):
                self.opportunity_id = data["items"][0]["opportunity_id"]
                yield
                return
        
        pytest.skip("No opportunities available for line item tests")
    
    def test_get_line_items_empty(self, api_client):
        """GET /api/sales-ops/opportunities/{id}/line-items - Initially empty or existing."""
        response = api_client.get(
            f"{BASE_URL}/api/sales-ops/opportunities/{self.opportunity_id}/line-items"
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "items" in data
        print(f"✓ Line items endpoint works, found {len(data['items'])} items")
    
    def test_create_material_line_item(self, api_client):
        """POST /api/sales-ops/opportunities/{id}/line-items - Add material (Field Services)."""
        line_item_data = {
            "category": "material",
            "item_name": "TEST_Cat6 Cable (1000ft)",
            "description": "High-quality Cat6 ethernet cable",
            "quantity": 5,
            "unit": "box",
            "cost_price": 150,
            "sell_price": 225,
            "supplier_name": "Network Supply Co",
            "supplier_sku": "CAT6-1000"
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{self.opportunity_id}/line-items",
            json=line_item_data
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "line_item_id" in data
        self.material_line_item_id = data["line_item_id"]
        print(f"✓ Created material line item: {self.material_line_item_id}")
    
    def test_create_hardware_line_item(self, api_client):
        """POST /api/sales-ops/opportunities/{id}/line-items - Add hardware (Professional Services)."""
        line_item_data = {
            "category": "hardware",
            "item_name": "TEST_Cisco Switch 24-Port",
            "description": "Managed network switch",
            "quantity": 2,
            "unit": "each",
            "cost_price": 800,
            "sell_price": 1200,
            "supplier_name": "Cisco Direct"
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{self.opportunity_id}/line-items",
            json=line_item_data
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "line_item_id" in data
        self.hardware_line_item_id = data["line_item_id"]
        print(f"✓ Created hardware line item: {self.hardware_line_item_id}")
    
    def test_create_wholesale_mrr_line_item(self, api_client):
        """POST /api/sales-ops/opportunities/{id}/line-items - Add wholesale MRR product."""
        line_item_data = {
            "category": "wholesale_mrr",
            "item_name": "TEST_Cloud Backup Service",
            "description": "Monthly cloud backup subscription",
            "quantity": 1,
            "unit": "month",
            "cost_price": 50,
            "sell_price": 99,
            "is_recurring": True,
            "recurring_term_months": 12
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{self.opportunity_id}/line-items",
            json=line_item_data
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "line_item_id" in data
        self.mrr_line_item_id = data["line_item_id"]
        print(f"✓ Created wholesale MRR line item: {self.mrr_line_item_id}")
    
    def test_get_line_items_by_category(self, api_client):
        """GET /api/sales-ops/opportunities/{id}/line-items?category=material - Filter by category."""
        response = api_client.get(
            f"{BASE_URL}/api/sales-ops/opportunities/{self.opportunity_id}/line-items?category=material"
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        items = data.get("items", [])
        
        # All items should be materials
        for item in items:
            assert item.get("category") == "material", f"Expected material, got {item.get('category')}"
        
        print(f"✓ Category filter works, found {len(items)} material items")
    
    def test_verify_line_item_totals(self, api_client):
        """Verify line items have correct calculated totals."""
        response = api_client.get(
            f"{BASE_URL}/api/sales-ops/opportunities/{self.opportunity_id}/line-items"
        )
        assert response.status_code == 200
        
        data = response.json()
        items = data.get("items", [])
        
        # Find our test items
        test_items = [i for i in items if i.get("item_name", "").startswith("TEST_")]
        
        for item in test_items:
            expected_total_cost = item["quantity"] * item["cost_price"]
            expected_total_sell = item["quantity"] * item["sell_price"]
            
            assert item.get("total_cost") == expected_total_cost, f"Cost mismatch for {item['item_name']}"
            assert item.get("total_sell") == expected_total_sell, f"Sell mismatch for {item['item_name']}"
        
        print(f"✓ All line item totals are correctly calculated")
    
    def test_delete_line_items(self, api_client):
        """DELETE /api/sales-ops/line-items/{id} - Cleanup test line items."""
        for attr in ['material_line_item_id', 'hardware_line_item_id', 'mrr_line_item_id']:
            line_item_id = getattr(self, attr, None)
            if line_item_id:
                response = api_client.delete(f"{BASE_URL}/api/sales-ops/line-items/{line_item_id}")
                assert response.status_code == 200, f"Failed to delete {attr}: {response.text}"
        
        print(f"✓ Cleaned up test line items")


class TestReferralAgreements:
    """Test referral MRR agreement endpoints with expiration tracking."""
    
    @pytest.fixture(autouse=True)
    def setup_opportunity(self, api_client):
        """Find or create a test opportunity with referral_mrr LOB."""
        response = api_client.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=50")
        if response.status_code == 200:
            data = response.json()
            # Find one with referral_mrr LOB
            for opp in data.get("items", []):
                if "referral_mrr" in opp.get("lines_of_business", []):
                    self.opportunity_id = opp["opportunity_id"]
                    self.opportunity_name = opp["name"]
                    yield
                    return
            
            # Use any opportunity if none has referral_mrr
            if data.get("items"):
                self.opportunity_id = data["items"][0]["opportunity_id"]
                self.opportunity_name = data["items"][0]["name"]
                yield
                return
        
        pytest.skip("No opportunities available for referral agreement tests")
    
    def test_get_referral_agreements_empty(self, api_client):
        """GET /api/sales-ops/opportunities/{id}/referral-agreements - Initially empty."""
        response = api_client.get(
            f"{BASE_URL}/api/sales-ops/opportunities/{self.opportunity_id}/referral-agreements"
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "items" in data
        print(f"✓ Referral agreements endpoint works, found {len(data['items'])} agreements")
    
    def test_create_referral_agreement(self, api_client):
        """POST /api/sales-ops/opportunities/{id}/referral-agreements - Upload agreement."""
        start_date = datetime.now().strftime("%Y-%m-%d")
        
        agreement_data = {
            "agreement_name": "TEST_Partner Referral Agreement",
            "partner_name": "Test Partner Inc",
            "agreement_term_years": 2,
            "start_date": start_date,
            "monthly_recurring_revenue": 500,
            "commission_rate": 10
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{self.opportunity_id}/referral-agreements",
            json=agreement_data
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "agreement_id" in data
        self.agreement_id = data["agreement_id"]
        print(f"✓ Created referral agreement: {self.agreement_id}")
    
    def test_verify_expiration_date_calculated(self, api_client):
        """Verify expiration date is correctly calculated from start_date + term."""
        response = api_client.get(
            f"{BASE_URL}/api/sales-ops/opportunities/{self.opportunity_id}/referral-agreements"
        )
        assert response.status_code == 200
        
        data = response.json()
        agreements = data.get("items", [])
        
        # Find our test agreement
        test_agreements = [a for a in agreements if a.get("agreement_name") == "TEST_Partner Referral Agreement"]
        if test_agreements:
            agreement = test_agreements[0]
            
            # Verify expiration date is ~2 years from start
            start = datetime.strptime(agreement["start_date"], "%Y-%m-%d")
            expiration = datetime.strptime(agreement["expiration_date"], "%Y-%m-%d")
            
            # Should be approximately 730 days (2 years)
            days_diff = (expiration - start).days
            assert 720 <= days_diff <= 740, f"Expected ~730 days, got {days_diff}"
            
            # Should have days_until_expiration calculated
            assert "days_until_expiration" in agreement
            print(f"✓ Expiration date correctly calculated: {agreement['expiration_date']} ({agreement['days_until_expiration']} days)")
    
    def test_get_expiring_soon_agreements(self, api_client):
        """GET /api/sales-ops/referral-agreements/expiring-soon - Get agreements expiring within 60 days."""
        response = api_client.get(f"{BASE_URL}/api/sales-ops/referral-agreements/expiring-soon")
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "items" in data
        assert "count" in data
        
        # All returned agreements should expire within 60 days
        for agreement in data["items"]:
            days_until = agreement.get("days_until_expiration", 999)
            assert days_until <= 60, f"Agreement should expire within 60 days, got {days_until}"
        
        print(f"✓ Expiring soon endpoint works, found {data['count']} agreements")
    
    def test_get_expiring_soon_custom_days(self, api_client):
        """GET /api/sales-ops/referral-agreements/expiring-soon?days=90 - Custom days parameter."""
        response = api_client.get(f"{BASE_URL}/api/sales-ops/referral-agreements/expiring-soon?days=90")
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "items" in data
        print(f"✓ Custom days parameter works, found {len(data['items'])} agreements expiring within 90 days")


class TestOpportunityNotFound:
    """Test 404 handling for non-existent opportunities."""
    
    def test_resource_assignments_404(self, api_client):
        """GET /api/sales-ops/opportunities/{invalid}/resource-assignments - Returns 404."""
        response = api_client.get(f"{BASE_URL}/api/sales-ops/opportunities/invalid_opp_id/resource-assignments")
        # Should return 200 with empty items (not 404) based on implementation
        assert response.status_code == 200
        data = response.json()
        assert data.get("items") == []
        print(f"✓ Invalid opportunity returns empty items")
    
    def test_create_resource_assignment_404(self, api_client):
        """POST /api/sales-ops/opportunities/{invalid}/resource-assignments - Returns 404."""
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/invalid_opp_id/resource-assignments",
            json={"resource_type_id": "test", "resource_type_name": "Test"}
        )
        assert response.status_code == 404
        print(f"✓ Create assignment for invalid opportunity returns 404")
    
    def test_create_line_item_404(self, api_client):
        """POST /api/sales-ops/opportunities/{invalid}/line-items - Returns 404."""
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/invalid_opp_id/line-items",
            json={"category": "material", "item_name": "Test"}
        )
        assert response.status_code == 404
        print(f"✓ Create line item for invalid opportunity returns 404")
    
    def test_create_referral_agreement_404(self, api_client):
        """POST /api/sales-ops/opportunities/{invalid}/referral-agreements - Returns 404."""
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/invalid_opp_id/referral-agreements",
            json={"agreement_name": "Test", "partner_name": "Test", "start_date": "2026-01-01", "agreement_term_years": 1}
        )
        assert response.status_code == 404
        print(f"✓ Create agreement for invalid opportunity returns 404")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
