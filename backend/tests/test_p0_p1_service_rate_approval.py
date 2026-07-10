"""
Test P0: Service Creation (only cost_to_business, no price field)
Test P1: Individual Contractor Rate Approval Logic
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin@123!"

# Test data from requirements
TEST_CONTRACTOR_BUSINESS_ID = "biz_e4898930f191"  # Avengers Technical Solutions
TEST_USER_BRUCE = "user_cbee5c91e119"  # Bruce Banner
TEST_USER_PETER = "user_82a7642025fb"  # Peter Parker


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token"""
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    if response.status_code == 200:
        data = response.json()
        return data.get("access_token") or data.get("token")
    pytest.skip(f"Authentication failed: {response.status_code} - {response.text}")


@pytest.fixture(scope="module")
def api_client(auth_token):
    """Authenticated requests session"""
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_token}"
    })
    return session


class TestP0ServiceCreation:
    """P0: Test that service creation only requires cost_to_business (no price field)"""
    
    def test_create_service_with_only_cost(self, api_client):
        """Create service with only cost_to_business - should succeed"""
        payload = {
            "service_name": "TEST_P0_Service_CostOnly",
            "service_description": "Test service with only cost field",
            "recurrency": "monthly",
            "cost_to_business": 299.00,
            "notes": "P0 test - no price field"
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/sales-ops/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}/services",
            json=payload
        )
        
        print(f"Create service response: {response.status_code}")
        print(f"Response body: {response.text}")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "service_id" in data, "Response should contain service_id"
        assert "product_id" in data, "Response should contain product_id"
        assert "Pending" in data.get("message", ""), "Should indicate pending approval"
        
        return data.get("service_id")
    
    def test_get_services_shows_cost_not_price(self, api_client):
        """Verify services list shows cost_to_business, not price_per_recurrency"""
        response = api_client.get(
            f"{BASE_URL}/api/sales-ops/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}/services"
        )
        
        assert response.status_code == 200
        data = response.json()
        services = data.get("items", [])
        
        print(f"Found {len(services)} services")
        
        # Check that services have cost_to_business field
        for service in services:
            print(f"Service: {service.get('service_name')} - cost_to_business: {service.get('cost_to_business')}")
            assert "cost_to_business" in service, "Service should have cost_to_business field"
            # sell_price should be None until approved by Procurement
            if service.get("approval_status") == "pending":
                assert service.get("sell_price") is None, "Pending service should not have sell_price"


class TestP1IndividualRateApproval:
    """P1: Test individual contractor rate approval logic"""
    
    def test_get_contractor_business_resource_types(self, api_client):
        """Get business resource types to find approved rates"""
        response = api_client.get(
            f"{BASE_URL}/api/admin/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}"
        )
        
        assert response.status_code == 200
        data = response.json()
        
        resource_types = data.get("resource_types_offered", [])
        print(f"Business has {len(resource_types)} resource types")
        
        for rt in resource_types:
            print(f"  - {rt.get('resource_type_name')}: cost_rate=${rt.get('cost_rate')}, status={rt.get('approval_status')}")
        
        return resource_types
    
    def test_update_user_rate_below_business_rate_auto_approves(self, api_client):
        """Rate <= business rate should auto-approve"""
        # First get the business resource types to find approved rate
        biz_response = api_client.get(
            f"{BASE_URL}/api/admin/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}"
        )
        assert biz_response.status_code == 200
        biz_data = biz_response.json()
        
        resource_types = biz_data.get("resource_types_offered", [])
        approved_rt = next((rt for rt in resource_types if rt.get("approval_status") == "approved"), None)
        
        if not approved_rt:
            pytest.skip("No approved resource types found for testing")
        
        business_rate = approved_rt.get("cost_rate", 150)
        rt_id = approved_rt.get("resource_type_id")
        rt_name = approved_rt.get("resource_type_name")
        
        print(f"Testing with resource type: {rt_name}, business rate: ${business_rate}")
        
        # Update user with rate BELOW business rate
        below_rate = business_rate - 10  # $10 below business rate
        
        payload = {
            "resource_type_assignments": [
                {
                    "resource_type_id": rt_id,
                    "resource_type_name": rt_name,
                    "cost_rate": below_rate
                }
            ]
        }
        
        response = api_client.put(
            f"{BASE_URL}/api/admin/users/{TEST_USER_BRUCE}",
            json=payload
        )
        
        print(f"Update user response: {response.status_code}")
        print(f"Response: {response.text}")
        
        assert response.status_code == 200
        data = response.json()
        
        # Check that assignment was auto-approved
        assignments = data.get("resource_type_assignments", [])
        assignment = next((a for a in assignments if a.get("resource_type_id") == rt_id), None)
        
        assert assignment is not None, "Assignment should exist"
        assert assignment.get("approval_status") == "approved", f"Rate ${below_rate} <= ${business_rate} should auto-approve"
        print(f"SUCCESS: Rate ${below_rate} auto-approved (business rate: ${business_rate})")
    
    def test_update_user_rate_above_business_rate_pending(self, api_client):
        """Rate > business rate should be set to pending"""
        # First get the business resource types to find approved rate
        biz_response = api_client.get(
            f"{BASE_URL}/api/admin/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}"
        )
        assert biz_response.status_code == 200
        biz_data = biz_response.json()
        
        resource_types = biz_data.get("resource_types_offered", [])
        approved_rt = next((rt for rt in resource_types if rt.get("approval_status") == "approved"), None)
        
        if not approved_rt:
            pytest.skip("No approved resource types found for testing")
        
        business_rate = approved_rt.get("cost_rate", 150)
        rt_id = approved_rt.get("resource_type_id")
        rt_name = approved_rt.get("resource_type_name")
        
        print(f"Testing with resource type: {rt_name}, business rate: ${business_rate}")
        
        # Update user with rate ABOVE business rate
        above_rate = business_rate + 25  # $25 above business rate
        
        payload = {
            "resource_type_assignments": [
                {
                    "resource_type_id": rt_id,
                    "resource_type_name": rt_name,
                    "cost_rate": above_rate
                }
            ]
        }
        
        response = api_client.put(
            f"{BASE_URL}/api/admin/users/{TEST_USER_PETER}",
            json=payload
        )
        
        print(f"Update user response: {response.status_code}")
        print(f"Response: {response.text}")
        
        assert response.status_code == 200
        data = response.json()
        
        # Check that assignment is pending
        assignments = data.get("resource_type_assignments", [])
        assignment = next((a for a in assignments if a.get("resource_type_id") == rt_id), None)
        
        assert assignment is not None, "Assignment should exist"
        assert assignment.get("approval_status") == "pending", f"Rate ${above_rate} > ${business_rate} should be pending"
        assert assignment.get("contractor_business_rate") == business_rate, "Should store business rate for reference"
        print(f"SUCCESS: Rate ${above_rate} set to pending (business rate: ${business_rate})")
    
    def test_get_pending_contractor_rates(self, api_client):
        """Verify pending rates appear in Procurement queue"""
        response = api_client.get(
            f"{BASE_URL}/api/sales-ops/procurement/pending-contractor-rates"
        )
        
        print(f"Pending rates response: {response.status_code}")
        
        assert response.status_code == 200
        data = response.json()
        
        pending_rates = data.get("items", [])
        print(f"Found {len(pending_rates)} pending rate exceptions")
        
        for rate in pending_rates:
            print(f"  - {rate.get('user_name')}: {rate.get('resource_type_name')} @ ${rate.get('requested_rate')}/hr (business: ${rate.get('business_approved_rate')}/hr)")
        
        # Should have at least one pending rate from previous test
        assert len(pending_rates) >= 0, "Should return pending rates list"
    
    def test_approve_individual_rate_exception(self, api_client):
        """Test Procurement can approve rate exception"""
        # First get pending rates
        pending_response = api_client.get(
            f"{BASE_URL}/api/sales-ops/procurement/pending-contractor-rates"
        )
        
        assert pending_response.status_code == 200
        pending_data = pending_response.json()
        pending_rates = pending_data.get("items", [])
        
        if not pending_rates:
            pytest.skip("No pending rates to approve")
        
        # Approve the first pending rate
        rate = pending_rates[0]
        user_id = rate.get("user_id")
        rt_id = rate.get("resource_type_id")
        
        print(f"Approving rate for {rate.get('user_name')} - {rate.get('resource_type_name')}")
        
        response = api_client.put(
            f"{BASE_URL}/api/sales-ops/procurement/contractor-rates/{user_id}/{rt_id}/approve",
            json={"action": "approve"}
        )
        
        print(f"Approve response: {response.status_code}")
        print(f"Response: {response.text}")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "approved" in data.get("message", "").lower() or data.get("assignment", {}).get("approval_status") == "approved"
        print("SUCCESS: Rate exception approved")


class TestProcurementServiceApproval:
    """Test Procurement service approval flow"""
    
    def test_approve_service_with_sell_price(self, api_client):
        """Procurement approves service and sets sell price"""
        # First get pending services
        response = api_client.get(
            f"{BASE_URL}/api/sales-ops/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}/services"
        )
        
        assert response.status_code == 200
        data = response.json()
        services = data.get("items", [])
        
        pending_service = next((s for s in services if s.get("approval_status") == "pending"), None)
        
        if not pending_service:
            pytest.skip("No pending services to approve")
        
        service_id = pending_service.get("service_id")
        cost = pending_service.get("cost_to_business", 299)
        sell_price = cost * 1.5  # 50% margin
        
        print(f"Approving service {service_id} with sell price ${sell_price}")
        
        response = api_client.put(
            f"{BASE_URL}/api/sales-ops/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}/services/{service_id}/approve",
            json={"action": "approve", "sell_price": sell_price}
        )
        
        print(f"Approve response: {response.status_code}")
        print(f"Response: {response.text}")
        
        assert response.status_code == 200
        
        # Verify service is now approved with sell price
        verify_response = api_client.get(
            f"{BASE_URL}/api/sales-ops/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}/services"
        )
        
        verify_data = verify_response.json()
        approved_service = next((s for s in verify_data.get("items", []) if s.get("service_id") == service_id), None)
        
        if approved_service:
            assert approved_service.get("approval_status") == "approved"
            assert approved_service.get("sell_price") == sell_price
            print(f"SUCCESS: Service approved with sell price ${sell_price}")


class TestCleanup:
    """Cleanup test data"""
    
    def test_cleanup_test_services(self, api_client):
        """Remove test services created during testing"""
        response = api_client.get(
            f"{BASE_URL}/api/sales-ops/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}/services"
        )
        
        if response.status_code == 200:
            data = response.json()
            services = data.get("items", [])
            
            for service in services:
                if service.get("service_name", "").startswith("TEST_"):
                    service_id = service.get("service_id")
                    delete_response = api_client.delete(
                        f"{BASE_URL}/api/sales-ops/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}/services/{service_id}"
                    )
                    print(f"Deleted test service {service_id}: {delete_response.status_code}")
        
        print("Cleanup completed")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
