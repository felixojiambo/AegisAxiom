"""
Opportunity to Deal Conversion - Backend API Tests
Tests the conversion workflow from Closed Won opportunities to Deals.
Phase 2 of Opportunity Management module.
"""
import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://zoho-connect-3.preview.emergentagent.com')


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token"""
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "support@simplimeta.io", "password": "Admin@123!"}
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    data = response.json()
    return data.get("token")


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    """Auth headers for API requests"""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture(scope="module")
def dropdown_data(auth_headers):
    """Get dropdown data for creating opportunities"""
    response = requests.get(
        f"{BASE_URL}/api/opportunities/dropdown-data/all",
        headers=auth_headers
    )
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def test_account_id(dropdown_data):
    """Get first available account ID for testing"""
    accounts = dropdown_data.get("accounts", [])
    assert len(accounts) > 0, "No accounts available for testing"
    return accounts[0]["account_id"]


@pytest.fixture
def test_partner_id(dropdown_data):
    """Get first available partner ID for agent of record"""
    partners = dropdown_data.get("partners", [])
    if len(partners) > 0:
        return partners[0]["partner_id"]
    return None


class TestConvertOpportunityToDeal:
    """Test opportunity to deal conversion endpoint"""
    
    def test_convert_closed_won_opportunity(self, auth_headers, test_account_id, test_partner_id):
        """POST /api/opportunities/{id}/convert-to-deal - Should convert closed_won opportunity to deal"""
        # Create a closed_won opportunity
        unique_name = f"TEST_ConvertToDeal_{uuid.uuid4().hex[:8]}"
        create_payload = {
            "name": unique_name,
            "account_id": test_account_id,
            "description": "Test opportunity for conversion",
            "priority": "high",
            "amount": 75000,
            "stage": "closed_won",  # Must be closed_won for conversion
            "lines_of_business": ["professional_services", "referral_mrr"],
            "commission_payment_method": "telarus",
            "is_test_data": True
        }
        
        if test_partner_id:
            create_payload["agent_of_record_id"] = test_partner_id
        
        create_response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json=create_payload,
            headers=auth_headers
        )
        assert create_response.status_code == 200, f"Create failed: {create_response.text}"
        opp_data = create_response.json()
        opp_id = opp_data["opportunity_id"]
        print(f"Created closed_won opportunity: {opp_id}")
        
        # Convert to deal
        convert_response = requests.post(
            f"{BASE_URL}/api/opportunities/{opp_id}/convert-to-deal",
            headers=auth_headers
        )
        assert convert_response.status_code == 200, f"Conversion failed: {convert_response.text}"
        convert_data = convert_response.json()
        
        # Verify conversion response
        assert "deal_id" in convert_data, "Response should contain deal_id"
        assert "opportunity_id" in convert_data, "Response should contain opportunity_id"
        assert convert_data["opportunity_id"] == opp_id
        deal_id = convert_data["deal_id"]
        print(f"Converted to deal: {deal_id}")
        
        # Verify opportunity is marked as converted
        opp_response = requests.get(
            f"{BASE_URL}/api/opportunities/{opp_id}",
            headers=auth_headers
        )
        assert opp_response.status_code == 200
        updated_opp = opp_response.json()
        assert updated_opp.get("converted_to_deal_id") == deal_id, "Opportunity should have converted_to_deal_id"
        assert updated_opp.get("converted_at") is not None, "Opportunity should have converted_at timestamp"
        print(f"Opportunity marked as converted: converted_to_deal_id={deal_id}")
        
        # Verify deal was created with correct data
        deal_response = requests.get(
            f"{BASE_URL}/api/deals/{deal_id}",
            headers=auth_headers
        )
        assert deal_response.status_code == 200, f"Failed to get deal: {deal_response.text}"
        deal_data = deal_response.json()
        
        # Verify deal data mapping
        assert deal_data.get("name") == unique_name, "Deal name should match opportunity name"
        assert deal_data.get("account_id") == test_account_id, "Deal account_id should match"
        assert deal_data.get("stage") == "Closed Won", "Deal stage should be 'Closed Won'"
        assert deal_data.get("amount") == 75000, "Deal amount should match opportunity amount"
        assert deal_data.get("is_test_data") == True, "Deal should preserve is_test_data flag"
        print(f"Deal created with correct data mapping")
        
        return opp_id, deal_id
    
    def test_convert_non_closed_won_opportunity_fails(self, auth_headers, test_account_id):
        """POST /api/opportunities/{id}/convert-to-deal - Should reject non-closed_won opportunities"""
        # Create a prospect_opp opportunity (not closed_won)
        unique_name = f"TEST_ConvertFail_{uuid.uuid4().hex[:8]}"
        create_response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "name": unique_name,
                "account_id": test_account_id,
                "amount": 50000,
                "stage": "prospect_opp",  # Not closed_won
                "is_test_data": True
            },
            headers=auth_headers
        )
        assert create_response.status_code == 200
        opp_id = create_response.json()["opportunity_id"]
        
        # Try to convert (should fail)
        convert_response = requests.post(
            f"{BASE_URL}/api/opportunities/{opp_id}/convert-to-deal",
            headers=auth_headers
        )
        assert convert_response.status_code == 400, "Should reject non-closed_won opportunity"
        error_data = convert_response.json()
        assert "Closed Won" in error_data.get("detail", ""), "Error should mention Closed Won requirement"
        print(f"Non-closed_won conversion rejected as expected: {error_data.get('detail')}")
    
    def test_prevent_duplicate_conversion(self, auth_headers, test_account_id):
        """POST /api/opportunities/{id}/convert-to-deal - Should prevent duplicate conversions"""
        # Create a closed_won opportunity
        unique_name = f"TEST_DuplicateConvert_{uuid.uuid4().hex[:8]}"
        create_response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "name": unique_name,
                "account_id": test_account_id,
                "amount": 30000,
                "stage": "closed_won",
                "is_test_data": True
            },
            headers=auth_headers
        )
        assert create_response.status_code == 200
        opp_id = create_response.json()["opportunity_id"]
        
        # First conversion (should succeed)
        first_convert = requests.post(
            f"{BASE_URL}/api/opportunities/{opp_id}/convert-to-deal",
            headers=auth_headers
        )
        assert first_convert.status_code == 200, "First conversion should succeed"
        deal_id = first_convert.json()["deal_id"]
        print(f"First conversion succeeded: {deal_id}")
        
        # Second conversion (should fail)
        second_convert = requests.post(
            f"{BASE_URL}/api/opportunities/{opp_id}/convert-to-deal",
            headers=auth_headers
        )
        assert second_convert.status_code == 400, "Second conversion should fail"
        error_data = second_convert.json()
        assert "already been converted" in error_data.get("detail", "").lower(), "Error should mention already converted"
        print(f"Duplicate conversion rejected as expected: {error_data.get('detail')}")
    
    def test_convert_nonexistent_opportunity(self, auth_headers):
        """POST /api/opportunities/{id}/convert-to-deal - Should return 404 for nonexistent opportunity"""
        fake_id = f"opp_nonexistent_{uuid.uuid4().hex[:8]}"
        convert_response = requests.post(
            f"{BASE_URL}/api/opportunities/{fake_id}/convert-to-deal",
            headers=auth_headers
        )
        assert convert_response.status_code == 404, "Should return 404 for nonexistent opportunity"
        print("Nonexistent opportunity conversion rejected with 404")


class TestConvertedDealInDealManagement:
    """Test that converted deals appear correctly in Deal Management"""
    
    def test_converted_deal_appears_in_deals_list(self, auth_headers, test_account_id):
        """Verify converted deal appears in /api/deals with correct status"""
        # Create and convert opportunity
        unique_name = f"TEST_DealList_{uuid.uuid4().hex[:8]}"
        create_response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "name": unique_name,
                "account_id": test_account_id,
                "amount": 45000,
                "stage": "closed_won",
                "is_test_data": True
            },
            headers=auth_headers
        )
        assert create_response.status_code == 200
        opp_id = create_response.json()["opportunity_id"]
        
        # Convert to deal
        convert_response = requests.post(
            f"{BASE_URL}/api/opportunities/{opp_id}/convert-to-deal",
            headers=auth_headers
        )
        assert convert_response.status_code == 200
        deal_id = convert_response.json()["deal_id"]
        
        # Check deal appears in deals list
        deals_response = requests.get(
            f"{BASE_URL}/api/deals?limit=100",
            headers=auth_headers
        )
        assert deals_response.status_code == 200
        deals_data = deals_response.json()
        
        # Find our deal
        found_deal = None
        for deal in deals_data.get("items", []):
            if deal.get("deal_id") == deal_id:
                found_deal = deal
                break
        
        assert found_deal is not None, f"Converted deal {deal_id} should appear in deals list"
        assert found_deal.get("stage") == "Closed Won", "Deal should have 'Closed Won' stage"
        assert found_deal.get("name") == unique_name, "Deal name should match"
        print(f"Converted deal found in deals list: {deal_id}")


class TestOpportunityConversionFields:
    """Test that opportunity model has conversion tracking fields"""
    
    def test_opportunity_has_conversion_fields(self, auth_headers, test_account_id):
        """Verify opportunity model includes converted_to_deal_id and converted_at fields"""
        # Create opportunity
        unique_name = f"TEST_ConversionFields_{uuid.uuid4().hex[:8]}"
        create_response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "name": unique_name,
                "account_id": test_account_id,
                "amount": 20000,
                "stage": "prospect_opp",
                "is_test_data": True
            },
            headers=auth_headers
        )
        assert create_response.status_code == 200
        opp_data = create_response.json()
        
        # Verify conversion fields exist (should be null/None initially)
        assert "converted_to_deal_id" in opp_data, "Opportunity should have converted_to_deal_id field"
        assert "converted_at" in opp_data, "Opportunity should have converted_at field"
        assert opp_data.get("converted_to_deal_id") is None, "converted_to_deal_id should be null initially"
        assert opp_data.get("converted_at") is None, "converted_at should be null initially"
        print("Opportunity has conversion tracking fields (initially null)")


class TestCleanupConversionTests:
    """Cleanup test data created during conversion tests"""
    
    def test_cleanup_test_opportunities_and_deals(self, auth_headers):
        """Delete test opportunities and deals created during testing"""
        # Cleanup test opportunities
        opps_response = requests.get(
            f"{BASE_URL}/api/opportunities?test_only=true&limit=100",
            headers=auth_headers
        )
        if opps_response.status_code == 200:
            opps_data = opps_response.json()
            test_opps = [o for o in opps_data.get("items", []) if o.get("name", "").startswith("TEST_")]
            
            for opp in test_opps:
                delete_response = requests.delete(
                    f"{BASE_URL}/api/opportunities/{opp['opportunity_id']}",
                    headers=auth_headers
                )
                if delete_response.status_code == 200:
                    print(f"Cleaned up opportunity: {opp['opportunity_id']}")
        
        # Cleanup test deals
        deals_response = requests.get(
            f"{BASE_URL}/api/deals?limit=100",
            headers=auth_headers
        )
        if deals_response.status_code == 200:
            deals_data = deals_response.json()
            test_deals = [d for d in deals_data.get("items", []) if d.get("name", "").startswith("TEST_")]
            
            for deal in test_deals:
                delete_response = requests.delete(
                    f"{BASE_URL}/api/deals/{deal['deal_id']}",
                    headers=auth_headers
                )
                if delete_response.status_code == 200:
                    print(f"Cleaned up deal: {deal['deal_id']}")
        
        print("Cleanup complete")
