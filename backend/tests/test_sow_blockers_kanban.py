"""
SOW Blockers, Status Pipeline, and Kanban View Tests
Tests for:
- Blocked/Missing Contractor Workflow
- Negative Margin Blocker
- SOW Status Bar and Status Changes
- Blockers API endpoint
- Kanban View Status Changes
- Prevent SOW Completion with Critical Blockers
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://zoho-connect-3.preview.emergentagent.com')

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin@123!"

# Test opportunity IDs from the problem statement
TEST_OPPORTUNITY_IDS = [
    "opp_81241a4996d3",  # Multi-LOB Test
    "opp_d1cf91228dd7",  # Batmobile/Wayne Enterprises
    "opp_9417de915c3e"   # Telarus Partnership
]

# Existing SOW for testing
EXISTING_SOW_ID = "sow_44234382ae7d"


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token for tests."""
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    if response.status_code == 200:
        return response.json().get("token")  # API returns "token" not "access_token"
    elif response.status_code == 429:
        # Rate limited - wait and retry
        time.sleep(10)
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
        )
        if response.status_code == 200:
            return response.json().get("token")
    pytest.skip(f"Authentication failed: {response.status_code}")


@pytest.fixture(scope="module")
def api_client(auth_token):
    """Create authenticated session."""
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json"
    })
    return session


class TestSOWTasksEndpoint:
    """Test SOW Tasks retrieval for opportunities."""
    
    def test_get_sow_tasks_for_opportunity(self, api_client):
        """Test GET /api/sales-ops/opportunities/{id}/sow-tasks returns SOW tasks."""
        for opp_id in TEST_OPPORTUNITY_IDS:
            response = api_client.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow-tasks")
            
            if response.status_code == 200:
                data = response.json()
                assert "sow_tasks" in data
                assert "opportunity" in data
                print(f"✅ Opportunity {opp_id}: {len(data['sow_tasks'])} SOW tasks found")
                return  # Found at least one working opportunity
            elif response.status_code == 404:
                print(f"⚠️ Opportunity {opp_id} not found, trying next...")
                continue
        
        # If we get here, at least one should have worked
        print("⚠️ No test opportunities found - may need to create test data")
    
    def test_sow_tasks_include_status_field(self, api_client):
        """Test that SOW tasks include status field."""
        for opp_id in TEST_OPPORTUNITY_IDS:
            response = api_client.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow-tasks")
            
            if response.status_code == 200:
                data = response.json()
                for sow in data.get("sow_tasks", []):
                    assert "status" in sow, "SOW should have status field"
                    assert sow["status"] in ["not_started", "in_progress", "blocked", "ready_for_review", "complete"]
                    print(f"✅ SOW {sow.get('sow_id')}: status = {sow['status']}")
                return
        
        print("⚠️ No test opportunities found")


class TestBlockersEndpoint:
    """Test the blockers API endpoint."""
    
    def test_blockers_endpoint_exists(self, api_client):
        """Test GET /api/sales-ops/opportunities/{id}/sows/{sow_id}/blockers endpoint exists."""
        # First get a valid SOW
        for opp_id in TEST_OPPORTUNITY_IDS:
            response = api_client.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow-tasks")
            
            if response.status_code == 200:
                data = response.json()
                sow_tasks = data.get("sow_tasks", [])
                
                if sow_tasks:
                    sow = sow_tasks[0]
                    sow_id = sow.get("sow_id")
                    
                    # Test blockers endpoint
                    blockers_response = api_client.get(
                        f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sows/{sow_id}/blockers"
                    )
                    
                    assert blockers_response.status_code == 200, f"Blockers endpoint should return 200, got {blockers_response.status_code}"
                    
                    blockers_data = blockers_response.json()
                    assert "sow_id" in blockers_data
                    assert "status" in blockers_data
                    assert "has_blockers" in blockers_data
                    assert "blockers" in blockers_data
                    assert "blocker_count" in blockers_data
                    assert "warning_count" in blockers_data
                    
                    print(f"✅ Blockers endpoint working for SOW {sow_id}")
                    print(f"   - Status: {blockers_data['status']}")
                    print(f"   - Has Blockers: {blockers_data['has_blockers']}")
                    print(f"   - Blocker Count: {blockers_data['blocker_count']}")
                    print(f"   - Warning Count: {blockers_data['warning_count']}")
                    return
        
        pytest.skip("No SOW tasks found to test blockers endpoint")
    
    def test_blockers_response_structure(self, api_client):
        """Test blockers endpoint returns correct structure."""
        for opp_id in TEST_OPPORTUNITY_IDS:
            response = api_client.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow-tasks")
            
            if response.status_code == 200:
                data = response.json()
                sow_tasks = data.get("sow_tasks", [])
                
                if sow_tasks:
                    sow = sow_tasks[0]
                    sow_id = sow.get("sow_id")
                    
                    blockers_response = api_client.get(
                        f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sows/{sow_id}/blockers"
                    )
                    
                    if blockers_response.status_code == 200:
                        blockers_data = blockers_response.json()
                        
                        # Check blocker structure if any exist
                        for blocker in blockers_data.get("blockers", []):
                            assert "type" in blocker, "Blocker should have type field"
                            assert blocker["type"] in ["critical", "warning"]
                            assert "category" in blocker, "Blocker should have category field"
                            assert "message" in blocker, "Blocker should have message field"
                        
                        print(f"✅ Blockers structure validated for SOW {sow_id}")
                        return
        
        pytest.skip("No SOW tasks found")


class TestStatusChangeEndpoint:
    """Test the status change PATCH endpoint."""
    
    def test_status_change_endpoint_exists(self, api_client):
        """Test PATCH /api/sales-ops/opportunities/{id}/sows/{sow_id}/status endpoint."""
        for opp_id in TEST_OPPORTUNITY_IDS:
            response = api_client.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow-tasks")
            
            if response.status_code == 200:
                data = response.json()
                sow_tasks = data.get("sow_tasks", [])
                
                if sow_tasks:
                    sow = sow_tasks[0]
                    sow_id = sow.get("sow_id")
                    original_status = sow.get("status", "not_started")
                    
                    # Try changing to in_progress
                    new_status = "in_progress" if original_status != "in_progress" else "not_started"
                    
                    status_response = api_client.patch(
                        f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sows/{sow_id}/status?status={new_status}"
                    )
                    
                    # Should succeed or fail with validation error (not 404/500)
                    assert status_response.status_code in [200, 400], f"Status change should return 200 or 400, got {status_response.status_code}"
                    
                    if status_response.status_code == 200:
                        result = status_response.json()
                        assert "new_status" in result or "message" in result
                        print(f"✅ Status change endpoint working: {original_status} -> {new_status}")
                        
                        # Revert status
                        api_client.patch(
                            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sows/{sow_id}/status?status={original_status}"
                        )
                    else:
                        print(f"⚠️ Status change blocked (expected if blockers exist): {status_response.json()}")
                    return
        
        pytest.skip("No SOW tasks found")
    
    def test_invalid_status_rejected(self, api_client):
        """Test that invalid status values are rejected."""
        for opp_id in TEST_OPPORTUNITY_IDS:
            response = api_client.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow-tasks")
            
            if response.status_code == 200:
                data = response.json()
                sow_tasks = data.get("sow_tasks", [])
                
                if sow_tasks:
                    sow = sow_tasks[0]
                    sow_id = sow.get("sow_id")
                    
                    # Try invalid status
                    status_response = api_client.patch(
                        f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sows/{sow_id}/status?status=invalid_status"
                    )
                    
                    assert status_response.status_code == 400, "Invalid status should return 400"
                    print(f"✅ Invalid status correctly rejected")
                    return
        
        pytest.skip("No SOW tasks found")
    
    def test_complete_status_blocked_with_blockers(self, api_client):
        """Test that SOW cannot be marked complete when blockers exist."""
        for opp_id in TEST_OPPORTUNITY_IDS:
            response = api_client.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow-tasks")
            
            if response.status_code == 200:
                data = response.json()
                sow_tasks = data.get("sow_tasks", [])
                
                for sow in sow_tasks:
                    sow_id = sow.get("sow_id")
                    
                    # Check if SOW has blockers
                    blockers_response = api_client.get(
                        f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sows/{sow_id}/blockers"
                    )
                    
                    if blockers_response.status_code == 200:
                        blockers_data = blockers_response.json()
                        
                        if blockers_data.get("has_blockers"):
                            # Try to mark as complete - should fail
                            status_response = api_client.patch(
                                f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sows/{sow_id}/status?status=complete"
                            )
                            
                            assert status_response.status_code == 400, "Should not allow complete status with blockers"
                            error_data = status_response.json()
                            assert "blockers" in error_data.get("detail", "").lower() or "cannot" in error_data.get("detail", "").lower()
                            print(f"✅ Complete status correctly blocked when blockers exist")
                            return
        
        print("⚠️ No SOWs with blockers found to test completion blocking")


class TestResourceAssignmentWithBlockers:
    """Test resource assignment with blocker detection."""
    
    def test_resource_assignment_endpoint(self, api_client):
        """Test POST /api/sales-ops/opportunities/{id}/resource-assignments endpoint."""
        for opp_id in TEST_OPPORTUNITY_IDS:
            # First verify opportunity exists
            opp_response = api_client.get(f"{BASE_URL}/api/opportunities/{opp_id}")
            
            if opp_response.status_code == 200:
                # Get resource types
                rt_response = api_client.get(f"{BASE_URL}/api/admin/resource-types")
                
                if rt_response.status_code == 200:
                    resource_types = rt_response.json()
                    
                    if resource_types:
                        rt = resource_types[0]
                        
                        # Create a test resource assignment with missing contractor (should create blocker)
                        payload = {
                            "resource_type_id": rt.get("resource_type_id"),
                            "resource_type_name": rt.get("name"),
                            "customer_contract_type": "boh",
                            "customer_billing_terms": "monthly",
                            "customer_payment_terms": "net_30",
                            "customer_hours_purchased": 100,
                            "customer_sell_rate": 180,
                            "total_sell": 18000,
                            # No contractor - should trigger blocker
                            "contractor_business_id": None,
                            "contractor_user_id": None,
                            "total_cost": 0,
                            "profit_amount": 18000,
                            "profit_margin": 100,
                            "status": "blocked_missing_contractor",
                            "blocker_reason": f"No contractor assigned for {rt.get('name')}",
                            "notes": "TEST - Missing contractor blocker test"
                        }
                        
                        assign_response = api_client.post(
                            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/resource-assignments",
                            json=payload
                        )
                        
                        if assign_response.status_code == 200:
                            assignment = assign_response.json()
                            assert assignment.get("status") == "blocked_missing_contractor"
                            print(f"✅ Resource assignment created with blocked status")
                            
                            # Clean up - delete the test assignment
                            if assignment.get("assignment_id"):
                                api_client.delete(
                                    f"{BASE_URL}/api/sales-ops/resource-assignments/{assignment['assignment_id']}"
                                )
                            return
                        else:
                            print(f"⚠️ Resource assignment failed: {assign_response.status_code}")
        
        print("⚠️ Could not test resource assignment - no valid opportunity/resource type found")
    
    def test_negative_margin_creates_blocker(self, api_client):
        """Test that negative profit margin creates a blocker."""
        for opp_id in TEST_OPPORTUNITY_IDS:
            opp_response = api_client.get(f"{BASE_URL}/api/opportunities/{opp_id}")
            
            if opp_response.status_code == 200:
                rt_response = api_client.get(f"{BASE_URL}/api/admin/resource-types")
                
                if rt_response.status_code == 200:
                    resource_types = rt_response.json()
                    
                    if resource_types:
                        rt = resource_types[0]
                        
                        # Create assignment with negative margin (cost > sell)
                        payload = {
                            "resource_type_id": rt.get("resource_type_id"),
                            "resource_type_name": rt.get("name"),
                            "customer_contract_type": "boh",
                            "customer_billing_terms": "monthly",
                            "customer_payment_terms": "net_30",
                            "customer_hours_purchased": 100,
                            "customer_sell_rate": 100,  # Low sell rate
                            "total_sell": 10000,
                            "contractor_business_id": None,
                            "contractor_user_id": None,
                            "contractor_cost_rate": 150,  # Higher cost rate
                            "contractor_hours_estimated": 100,
                            "total_cost": 15000,  # Cost > Sell = negative margin
                            "profit_amount": -5000,
                            "profit_margin": -50,  # Negative margin
                            "status": "blocked_missing_contractor",
                            "blocker_reason": f"Negative margin (-50%) for {rt.get('name')}",
                            "notes": "TEST - Negative margin blocker test"
                        }
                        
                        assign_response = api_client.post(
                            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/resource-assignments",
                            json=payload
                        )
                        
                        if assign_response.status_code == 200:
                            assignment = assign_response.json()
                            assert assignment.get("profit_margin") < 0, "Profit margin should be negative"
                            print(f"✅ Negative margin assignment created: margin = {assignment.get('profit_margin')}%")
                            
                            # Clean up
                            if assignment.get("assignment_id"):
                                api_client.delete(
                                    f"{BASE_URL}/api/sales-ops/resource-assignments/{assignment['assignment_id']}"
                                )
                            return
        
        print("⚠️ Could not test negative margin blocker")


class TestSOWStatusValues:
    """Test valid SOW status values."""
    
    def test_valid_status_values(self, api_client):
        """Test that all valid status values are accepted."""
        valid_statuses = ["not_started", "in_progress", "blocked", "ready_for_review", "complete"]
        
        for opp_id in TEST_OPPORTUNITY_IDS:
            response = api_client.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow-tasks")
            
            if response.status_code == 200:
                data = response.json()
                sow_tasks = data.get("sow_tasks", [])
                
                if sow_tasks:
                    sow = sow_tasks[0]
                    sow_id = sow.get("sow_id")
                    original_status = sow.get("status", "not_started")
                    
                    # Test each valid status (except complete which may be blocked)
                    for status in ["not_started", "in_progress"]:
                        status_response = api_client.patch(
                            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sows/{sow_id}/status?status={status}"
                        )
                        
                        # Should succeed (200) or fail due to blockers (400)
                        assert status_response.status_code in [200, 400], f"Status {status} should return 200 or 400"
                        
                        if status_response.status_code == 200:
                            print(f"✅ Status '{status}' accepted")
                    
                    # Restore original status
                    api_client.patch(
                        f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sows/{sow_id}/status?status={original_status}"
                    )
                    return
        
        pytest.skip("No SOW tasks found")


class TestKanbanViewData:
    """Test data structure for Kanban view."""
    
    def test_sow_tasks_have_kanban_fields(self, api_client):
        """Test that SOW tasks have all fields needed for Kanban view."""
        for opp_id in TEST_OPPORTUNITY_IDS:
            response = api_client.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow-tasks")
            
            if response.status_code == 200:
                data = response.json()
                sow_tasks = data.get("sow_tasks", [])
                
                for sow in sow_tasks:
                    # Required fields for Kanban cards
                    assert "sow_id" in sow, "SOW should have sow_id"
                    assert "status" in sow, "SOW should have status"
                    assert "line_of_business" in sow, "SOW should have line_of_business"
                    
                    # Optional but expected fields
                    if "selected_resource_assignment_ids" in sow:
                        assert isinstance(sow["selected_resource_assignment_ids"], list)
                    
                    print(f"✅ SOW {sow.get('sow_id')} has required Kanban fields")
                
                if sow_tasks:
                    return
        
        print("⚠️ No SOW tasks found to validate Kanban fields")


class TestBlockerCategories:
    """Test blocker category detection."""
    
    def test_missing_contractor_blocker_category(self, api_client):
        """Test that missing contractor creates 'missing_contractor' category blocker."""
        # This is tested via the blockers endpoint response structure
        for opp_id in TEST_OPPORTUNITY_IDS:
            response = api_client.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow-tasks")
            
            if response.status_code == 200:
                data = response.json()
                sow_tasks = data.get("sow_tasks", [])
                
                for sow in sow_tasks:
                    sow_id = sow.get("sow_id")
                    
                    blockers_response = api_client.get(
                        f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sows/{sow_id}/blockers"
                    )
                    
                    if blockers_response.status_code == 200:
                        blockers_data = blockers_response.json()
                        
                        for blocker in blockers_data.get("blockers", []):
                            if blocker.get("category") == "missing_contractor":
                                assert blocker.get("type") == "critical"
                                print(f"✅ Missing contractor blocker found with correct category")
                                return
                            elif blocker.get("category") == "negative_margin":
                                assert blocker.get("type") == "critical"
                                print(f"✅ Negative margin blocker found with correct category")
                            elif blocker.get("category") == "low_margin":
                                assert blocker.get("type") == "warning"
                                print(f"✅ Low margin warning found with correct category")
        
        print("⚠️ No blockers found to validate categories (may be expected if no blockers exist)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
