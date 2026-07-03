"""
Test Suite for Solutions Design - SOW (Statement of Work) Builder
Phase 3B: Tests for GET/POST /api/sales-ops/opportunities/{id}/sow endpoints
"""
import pytest
import requests
import os
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin@123!"
TEST_OPPORTUNITY_ID = "opp_9417de915c3e"


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token for testing."""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })
    assert response.status_code == 200, f"Login failed: {response.text}"
    data = response.json()
    # API returns 'token' not 'access_token'
    assert "token" in data, "No token in login response"
    return data["token"]


@pytest.fixture(scope="module")
def api_client(auth_token):
    """Create authenticated session."""
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_token}"
    })
    return session


class TestSOWEndpoints:
    """Test SOW CRUD operations."""
    
    def test_get_sow_for_opportunity(self, api_client):
        """Test GET /api/sales-ops/opportunities/{id}/sow returns SOW data."""
        response = api_client.get(f"{BASE_URL}/api/sales-ops/opportunities/{TEST_OPPORTUNITY_ID}/sow")
        assert response.status_code == 200, f"GET SOW failed: {response.text}"
        
        data = response.json()
        # Verify SOW structure
        assert "opportunity_id" in data
        assert data["opportunity_id"] == TEST_OPPORTUNITY_ID
        
        # Verify SMART fields exist
        assert "smart_specific" in data
        assert "smart_measurable" in data
        assert "smart_achievable" in data
        assert "smart_relevant" in data
        assert "smart_time_bound" in data
        
        # Verify contract config fields
        assert "contract_type" in data
        assert "service_categories" in data
        assert "billing_terms" in data
        assert "payment_terms" in data
        assert "start_date" in data
        assert "end_date" in data
        
        # Verify milestones array
        assert "milestones" in data
        assert isinstance(data["milestones"], list)
        
        # Verify billing details
        assert "billing_account_email" in data
        assert "billing_account_phone" in data
        assert "special_billing_instructions" in data
        
        # Verify BOA authorized users
        assert "boa_authorized_users" in data
        assert isinstance(data["boa_authorized_users"], list)
        
        # Verify status and version
        assert "status" in data
        assert "version" in data
        
        print(f"✅ GET SOW returned valid structure with version {data.get('version')}")
    
    def test_get_sow_for_nonexistent_opportunity(self, api_client):
        """Test GET SOW for non-existent opportunity returns 404."""
        response = api_client.get(f"{BASE_URL}/api/sales-ops/opportunities/nonexistent_opp_id/sow")
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✅ GET SOW for non-existent opportunity returns 404")
    
    def test_create_or_update_sow_smart_fields(self, api_client):
        """Test POST SOW with SMART proposal fields."""
        sow_data = {
            "smart_specific": "Deliver a comprehensive network infrastructure upgrade",
            "smart_measurable": "Achieve 99.9% uptime and 50% latency reduction",
            "smart_achievable": "Using existing team with 3 senior engineers",
            "smart_relevant": "Supports client's digital transformation initiative",
            "smart_time_bound": "Complete within 90 days from contract signing"
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{TEST_OPPORTUNITY_ID}/sow",
            json=sow_data
        )
        assert response.status_code == 200, f"POST SOW failed: {response.text}"
        
        data = response.json()
        assert data["smart_specific"] == sow_data["smart_specific"]
        assert data["smart_measurable"] == sow_data["smart_measurable"]
        assert data["smart_achievable"] == sow_data["smart_achievable"]
        assert data["smart_relevant"] == sow_data["smart_relevant"]
        assert data["smart_time_bound"] == sow_data["smart_time_bound"]
        
        print("✅ POST SOW with SMART fields saved correctly")
    
    def test_update_sow_contract_config(self, api_client):
        """Test POST SOW with contract configuration."""
        sow_data = {
            "contract_type": "fixed_fee",
            "service_categories": ["professional_services", "field_services"],
            "billing_terms": "monthly",
            "payment_terms": "net_30",
            "start_date": "2026-04-01",
            "end_date": "2026-06-30",
            "total_amount": 75000.00
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{TEST_OPPORTUNITY_ID}/sow",
            json=sow_data
        )
        assert response.status_code == 200, f"POST SOW failed: {response.text}"
        
        data = response.json()
        assert data["contract_type"] == "fixed_fee"
        assert "professional_services" in data["service_categories"]
        assert "field_services" in data["service_categories"]
        assert data["billing_terms"] == "monthly"
        assert data["payment_terms"] == "net_30"
        assert data["start_date"] == "2026-04-01"
        assert data["end_date"] == "2026-06-30"
        assert data["total_amount"] == 75000.00
        
        print("✅ POST SOW with contract config saved correctly")
    
    def test_update_sow_monthly_contract(self, api_client):
        """Test POST SOW with monthly contract type shows monthly_rate."""
        sow_data = {
            "contract_type": "monthly",
            "monthly_rate": 5000.00,
            "billing_terms": "monthly",
            "payment_terms": "net_14"
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{TEST_OPPORTUNITY_ID}/sow",
            json=sow_data
        )
        assert response.status_code == 200, f"POST SOW failed: {response.text}"
        
        data = response.json()
        assert data["contract_type"] == "monthly"
        assert data["monthly_rate"] == 5000.00
        
        print("✅ POST SOW with monthly contract type saved correctly")
    
    def test_update_sow_with_milestones(self, api_client):
        """Test POST SOW with milestones and bullets."""
        sow_data = {
            "milestones": [
                {
                    "milestone_id": "ms_test_001",
                    "milestone_name": "Phase 1: Discovery & Planning",
                    "order": 0,
                    "bullets": [
                        {
                            "bullet_id": "bullet_test_001",
                            "bullet_text": "Conduct stakeholder interviews",
                            "estimated_hours": 8.0,
                            "deliverable": "Interview summary document",
                            "order": 0
                        },
                        {
                            "bullet_id": "bullet_test_002",
                            "bullet_text": "Document current infrastructure",
                            "estimated_hours": 16.0,
                            "deliverable": "Infrastructure assessment report",
                            "order": 1
                        }
                    ]
                },
                {
                    "milestone_id": "ms_test_002",
                    "milestone_name": "Phase 2: Implementation",
                    "order": 1,
                    "bullets": [
                        {
                            "bullet_id": "bullet_test_003",
                            "bullet_text": "Deploy network equipment",
                            "estimated_hours": 24.0,
                            "deliverable": "Deployed and configured equipment",
                            "order": 0
                        }
                    ]
                }
            ]
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{TEST_OPPORTUNITY_ID}/sow",
            json=sow_data
        )
        assert response.status_code == 200, f"POST SOW failed: {response.text}"
        
        data = response.json()
        assert len(data["milestones"]) == 2
        
        # Verify first milestone
        ms1 = data["milestones"][0]
        assert ms1["milestone_name"] == "Phase 1: Discovery & Planning"
        assert len(ms1["bullets"]) == 2
        assert ms1["bullets"][0]["bullet_text"] == "Conduct stakeholder interviews"
        assert ms1["bullets"][0]["estimated_hours"] == 8.0
        assert ms1["bullets"][0]["deliverable"] == "Interview summary document"
        
        # Verify second milestone
        ms2 = data["milestones"][1]
        assert ms2["milestone_name"] == "Phase 2: Implementation"
        assert len(ms2["bullets"]) == 1
        
        print("✅ POST SOW with milestones and bullets saved correctly")
    
    def test_update_sow_billing_details(self, api_client):
        """Test POST SOW with billing contact details."""
        sow_data = {
            "billing_account_email": "billing@testclient.com",
            "billing_account_phone": "555-123-4567",
            "special_billing_instructions": "Include PO number on all invoices"
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{TEST_OPPORTUNITY_ID}/sow",
            json=sow_data
        )
        assert response.status_code == 200, f"POST SOW failed: {response.text}"
        
        data = response.json()
        assert data["billing_account_email"] == "billing@testclient.com"
        assert data["billing_account_phone"] == "555-123-4567"
        assert data["special_billing_instructions"] == "Include PO number on all invoices"
        
        print("✅ POST SOW with billing details saved correctly")
    
    def test_update_sow_boa_authorized_users(self, api_client):
        """Test POST SOW with BOA authorized users (only for BOA contract type)."""
        sow_data = {
            "contract_type": "boa",
            "boa_authorized_users": [
                {
                    "user_id": "auth_user_test_001",
                    "name": "John Smith",
                    "email": "john.smith@testclient.com",
                    "phone": "555-111-2222",
                    "max_hours_allowed": 40.0
                },
                {
                    "user_id": "auth_user_test_002",
                    "name": "Jane Doe",
                    "email": "jane.doe@testclient.com",
                    "phone": "555-333-4444",
                    "max_hours_allowed": 20.0
                }
            ]
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{TEST_OPPORTUNITY_ID}/sow",
            json=sow_data
        )
        assert response.status_code == 200, f"POST SOW failed: {response.text}"
        
        data = response.json()
        assert data["contract_type"] == "boa"
        assert len(data["boa_authorized_users"]) == 2
        
        # Verify first authorized user
        user1 = data["boa_authorized_users"][0]
        assert user1["name"] == "John Smith"
        assert user1["email"] == "john.smith@testclient.com"
        assert user1["max_hours_allowed"] == 40.0
        
        print("✅ POST SOW with BOA authorized users saved correctly")
    
    def test_sow_version_increments_on_update(self, api_client):
        """Test that SOW version increments on each update."""
        # Get current version
        response = api_client.get(f"{BASE_URL}/api/sales-ops/opportunities/{TEST_OPPORTUNITY_ID}/sow")
        assert response.status_code == 200
        current_version = response.json().get("version", 1)
        
        # Update SOW
        sow_data = {
            "smart_specific": f"Updated at {datetime.now().isoformat()}"
        }
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{TEST_OPPORTUNITY_ID}/sow",
            json=sow_data
        )
        assert response.status_code == 200
        
        new_version = response.json().get("version", 1)
        assert new_version == current_version + 1, f"Version should increment: {current_version} -> {new_version}"
        
        print(f"✅ SOW version incremented from {current_version} to {new_version}")
    
    def test_sow_complete_workflow(self, api_client):
        """Test complete SOW creation workflow with all fields."""
        complete_sow = {
            # SMART Proposal
            "smart_specific": "Complete network infrastructure overhaul",
            "smart_measurable": "99.9% uptime, 50% latency reduction",
            "smart_achievable": "Team of 5 engineers over 3 months",
            "smart_relevant": "Supports digital transformation goals",
            "smart_time_bound": "90 days from contract signing",
            
            # Contract Config
            "contract_type": "boh",
            "service_categories": ["professional_services"],
            "billing_terms": "biweekly",
            "payment_terms": "net_30",
            "start_date": "2026-04-01",
            "end_date": "2026-07-01",
            
            # Milestones
            "milestones": [
                {
                    "milestone_id": "ms_complete_001",
                    "milestone_name": "Discovery",
                    "order": 0,
                    "bullets": [
                        {
                            "bullet_id": "bullet_complete_001",
                            "bullet_text": "Requirements gathering",
                            "estimated_hours": 16.0,
                            "deliverable": "Requirements document",
                            "order": 0
                        }
                    ]
                }
            ],
            
            # Billing Details
            "billing_account_email": "ap@client.com",
            "billing_account_phone": "555-999-8888",
            "special_billing_instructions": "Net 30 terms",
            
            # Empty authorized users (not BOA)
            "boa_authorized_users": []
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{TEST_OPPORTUNITY_ID}/sow",
            json=complete_sow
        )
        assert response.status_code == 200, f"Complete SOW save failed: {response.text}"
        
        data = response.json()
        
        # Verify all sections saved
        assert data["smart_specific"] == complete_sow["smart_specific"]
        assert data["contract_type"] == "boh"
        assert len(data["milestones"]) == 1
        assert data["billing_account_email"] == "ap@client.com"
        assert data["status"] == "draft"
        
        print("✅ Complete SOW workflow test passed")


class TestSOWEdgeCases:
    """Test edge cases and error handling."""
    
    def test_sow_empty_milestones(self, api_client):
        """Test SOW with empty milestones array."""
        sow_data = {
            "milestones": []
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{TEST_OPPORTUNITY_ID}/sow",
            json=sow_data
        )
        assert response.status_code == 200
        assert response.json()["milestones"] == []
        print("✅ SOW with empty milestones saved correctly")
    
    def test_sow_empty_service_categories(self, api_client):
        """Test SOW with empty service categories."""
        sow_data = {
            "service_categories": []
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{TEST_OPPORTUNITY_ID}/sow",
            json=sow_data
        )
        assert response.status_code == 200
        assert response.json()["service_categories"] == []
        print("✅ SOW with empty service categories saved correctly")
    
    def test_sow_null_optional_fields(self, api_client):
        """Test SOW with null optional fields."""
        sow_data = {
            "total_amount": None,
            "monthly_rate": None,
            "start_date": None,
            "end_date": None
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{TEST_OPPORTUNITY_ID}/sow",
            json=sow_data
        )
        assert response.status_code == 200
        print("✅ SOW with null optional fields saved correctly")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
