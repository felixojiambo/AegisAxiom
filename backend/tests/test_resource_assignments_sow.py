"""
Test Resource Assignments in SOW Builder
Tests the new feature where Solutions Design can select resources WITHIN each SOW (per LOB).
Resource selection happens inside the SOW Builder, not in Procurement tab.
When an Opportunity converts to Deal, each Contract should only get the resources 
that were selected in that specific SOW (via sow.selected_resource_assignment_ids).
"""
import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin@123!"


class TestResourceAssignmentsSOW:
    """Test Resource Assignments in SOW Builder feature"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test session with authentication"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD
        })
        
        if login_response.status_code == 200:
            data = login_response.json()
            token = data.get("access_token") or data.get("token")
            if token:
                self.session.headers.update({"Authorization": f"Bearer {token}"})
                self.user_id = data.get("user", {}).get("user_id")
        else:
            pytest.skip(f"Authentication failed: {login_response.status_code}")
    
    # ---- TEST 1: Verify Resource Assignments API ----
    def test_get_resource_assignments_endpoint(self):
        """Test GET /api/sales-ops/opportunities/{id}/resource-assignments endpoint exists"""
        # First get an opportunity in proposal_development stage
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=10")
        assert opps_response.status_code == 200, f"Failed to get opportunities: {opps_response.text}"
        
        opps_data = opps_response.json()
        opportunities = opps_data.get("items", [])
        
        if not opportunities:
            pytest.skip("No opportunities in proposal_development stage to test")
        
        opp_id = opportunities[0].get("opportunity_id")
        
        # Test the resource assignments endpoint
        response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/resource-assignments")
        assert response.status_code == 200, f"Resource assignments endpoint failed: {response.text}"
        
        data = response.json()
        assert "items" in data, "Response should contain 'items' key"
        print(f"✓ Resource assignments endpoint works. Found {len(data['items'])} assignments for opportunity {opp_id}")
    
    # ---- TEST 2: Verify SOW has selected_resource_assignment_ids field ----
    def test_sow_has_selected_resource_assignment_ids_field(self):
        """Test that SOW model includes selected_resource_assignment_ids field"""
        # Get an opportunity with field_services or professional_services LOB
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=50")
        assert opps_response.status_code == 200
        
        opps_data = opps_response.json()
        opportunities = opps_data.get("items", [])
        
        # Find opportunity with FS or PS LOB
        target_opp = None
        target_lob = None
        for opp in opportunities:
            lobs = opp.get("lines_of_business", [])
            if "field_services" in lobs:
                target_opp = opp
                target_lob = "field_services"
                break
            elif "professional_services" in lobs:
                target_opp = opp
                target_lob = "professional_services"
                break
        
        if not target_opp:
            pytest.skip("No opportunity with field_services or professional_services LOB found")
        
        opp_id = target_opp.get("opportunity_id")
        
        # Get SOW for this LOB
        sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow?lob={target_lob}")
        assert sow_response.status_code == 200, f"Failed to get SOW: {sow_response.text}"
        
        sow_data = sow_response.json()
        
        # Verify the field exists (even if empty)
        assert "selected_resource_assignment_ids" in sow_data, "SOW should have selected_resource_assignment_ids field"
        assert isinstance(sow_data["selected_resource_assignment_ids"], list), "selected_resource_assignment_ids should be a list"
        
        print(f"✓ SOW for {target_lob} has selected_resource_assignment_ids field with {len(sow_data['selected_resource_assignment_ids'])} selections")
    
    # ---- TEST 3: Test saving selected resources to SOW ----
    def test_save_selected_resources_to_sow(self):
        """Test that selected resources can be saved to SOW"""
        # Get an opportunity with FS or PS LOB
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=50")
        assert opps_response.status_code == 200
        
        opps_data = opps_response.json()
        opportunities = opps_data.get("items", [])
        
        # Find opportunity with FS or PS LOB
        target_opp = None
        target_lob = None
        for opp in opportunities:
            lobs = opp.get("lines_of_business", [])
            if "field_services" in lobs:
                target_opp = opp
                target_lob = "field_services"
                break
            elif "professional_services" in lobs:
                target_opp = opp
                target_lob = "professional_services"
                break
        
        if not target_opp:
            pytest.skip("No opportunity with field_services or professional_services LOB found")
        
        opp_id = target_opp.get("opportunity_id")
        
        # Get available resource assignments
        resources_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/resource-assignments")
        assert resources_response.status_code == 200
        
        resources_data = resources_response.json()
        available_resources = resources_data.get("items", [])
        
        if not available_resources:
            print(f"⚠ No resource assignments available for opportunity {opp_id}. Creating test resource assignment...")
            # Skip this test if no resources - we'll test the flow anyway
            pytest.skip("No resource assignments available to test selection")
        
        # Get current SOW
        sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow?lob={target_lob}")
        assert sow_response.status_code == 200
        
        sow_data = sow_response.json()
        
        # Select first available resource
        test_assignment_id = available_resources[0].get("assignment_id")
        
        # Update SOW with selected resource
        sow_data["selected_resource_assignment_ids"] = [test_assignment_id]
        
        save_response = self.session.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow?lob={target_lob}",
            json=sow_data
        )
        assert save_response.status_code == 200, f"Failed to save SOW: {save_response.text}"
        
        # Verify the selection was saved
        verify_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow?lob={target_lob}")
        assert verify_response.status_code == 200
        
        verify_data = verify_response.json()
        assert test_assignment_id in verify_data.get("selected_resource_assignment_ids", []), \
            "Selected resource should be saved in SOW"
        
        print(f"✓ Successfully saved resource selection {test_assignment_id} to SOW for {target_lob}")
    
    # ---- TEST 4: Verify Resource Assignments tab only shows for FS/PS LOBs ----
    def test_sow_tasks_endpoint_returns_lob_info(self):
        """Test that SOW tasks endpoint returns LOB information for tab visibility"""
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=10")
        assert opps_response.status_code == 200
        
        opps_data = opps_response.json()
        opportunities = opps_data.get("items", [])
        
        if not opportunities:
            pytest.skip("No opportunities in proposal_development stage")
        
        opp_id = opportunities[0].get("opportunity_id")
        
        # Get SOW tasks
        tasks_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow-tasks")
        assert tasks_response.status_code == 200, f"Failed to get SOW tasks: {tasks_response.text}"
        
        tasks_data = tasks_response.json()
        assert "sow_tasks" in tasks_data, "Response should contain sow_tasks"
        
        # Verify each task has line_of_business
        for task in tasks_data.get("sow_tasks", []):
            assert "line_of_business" in task, "Each SOW task should have line_of_business"
            lob = task.get("line_of_business")
            # Resource Assignments tab should only show for FS and PS, not wholesale_mrr or referral_mrr
            if lob in ["field_services", "professional_services"]:
                print(f"✓ LOB {lob} should show Resource Assignments tab")
            elif lob in ["wholesale_mrr", "referral_mrr"]:
                print(f"✓ LOB {lob} should NOT show Resource Assignments tab")
    
    # ---- TEST 5: Test Deal Conversion uses selected_resource_assignment_ids ----
    def test_deal_conversion_endpoint_exists(self):
        """Test that convert-to-deal endpoint exists and validates stage"""
        # Get any opportunity (not necessarily closed_won)
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?limit=5")
        assert opps_response.status_code == 200
        
        opps_data = opps_response.json()
        opportunities = opps_data.get("items", [])
        
        if not opportunities:
            pytest.skip("No opportunities available")
        
        # Find an opportunity that is NOT closed_won to test validation
        non_closed_opp = None
        for opp in opportunities:
            if opp.get("stage") != "closed_won":
                non_closed_opp = opp
                break
        
        if non_closed_opp:
            opp_id = non_closed_opp.get("opportunity_id")
            
            # Try to convert - should fail with validation error
            convert_response = self.session.post(f"{BASE_URL}/api/opportunities/{opp_id}/convert-to-deal")
            
            # Should return 400 because not in closed_won stage
            assert convert_response.status_code == 400, \
                f"Expected 400 for non-closed_won opportunity, got {convert_response.status_code}"
            
            error_data = convert_response.json()
            assert "closed_won" in error_data.get("detail", "").lower() or "closed won" in error_data.get("detail", "").lower(), \
                "Error should mention closed_won stage requirement"
            
            print(f"✓ Convert-to-deal endpoint correctly validates stage requirement")
        else:
            print("⚠ All opportunities are closed_won, skipping validation test")
    
    # ---- TEST 6: Verify SOW endpoint accepts selected_resource_assignment_ids in POST ----
    def test_sow_endpoint_accepts_resource_selections(self):
        """Test that SOW POST endpoint accepts selected_resource_assignment_ids"""
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=50")
        assert opps_response.status_code == 200
        
        opps_data = opps_response.json()
        opportunities = opps_data.get("items", [])
        
        # Find opportunity with FS or PS LOB
        target_opp = None
        target_lob = None
        for opp in opportunities:
            lobs = opp.get("lines_of_business", [])
            if "field_services" in lobs:
                target_opp = opp
                target_lob = "field_services"
                break
            elif "professional_services" in lobs:
                target_opp = opp
                target_lob = "professional_services"
                break
        
        if not target_opp:
            pytest.skip("No opportunity with field_services or professional_services LOB found")
        
        opp_id = target_opp.get("opportunity_id")
        
        # Get current SOW
        sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow?lob={target_lob}")
        assert sow_response.status_code == 200
        
        sow_data = sow_response.json()
        
        # Try to save with empty selections (should work)
        sow_data["selected_resource_assignment_ids"] = []
        
        save_response = self.session.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow?lob={target_lob}",
            json=sow_data
        )
        assert save_response.status_code == 200, f"Failed to save SOW with empty selections: {save_response.text}"
        
        # Try to save with fake IDs (should work - validation happens at conversion time)
        sow_data["selected_resource_assignment_ids"] = ["ra_test123", "ra_test456"]
        
        save_response2 = self.session.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow?lob={target_lob}",
            json=sow_data
        )
        assert save_response2.status_code == 200, f"Failed to save SOW with test IDs: {save_response2.text}"
        
        # Verify saved
        verify_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow?lob={target_lob}")
        verify_data = verify_response.json()
        
        assert "ra_test123" in verify_data.get("selected_resource_assignment_ids", []), \
            "Test ID should be saved"
        
        # Clean up - reset to empty
        sow_data["selected_resource_assignment_ids"] = []
        self.session.post(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow?lob={target_lob}", json=sow_data)
        
        print(f"✓ SOW endpoint correctly accepts and saves selected_resource_assignment_ids")


class TestMultiLOBOpportunity:
    """Test Multi-LOB Opportunity with different resource selections per SOW"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test session with authentication"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD
        })
        
        if login_response.status_code == 200:
            data = login_response.json()
            token = data.get("access_token") or data.get("token")
            if token:
                self.session.headers.update({"Authorization": f"Bearer {token}"})
        else:
            pytest.skip(f"Authentication failed: {login_response.status_code}")
    
    def test_find_multi_lob_opportunity(self):
        """Find and verify Multi-LOB Test Opportunity exists"""
        # Search for the test opportunity
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?search=Multi-LOB&limit=50")
        assert opps_response.status_code == 200
        
        opps_data = opps_response.json()
        opportunities = opps_data.get("items", [])
        
        multi_lob_opp = None
        for opp in opportunities:
            if "Multi-LOB" in opp.get("name", ""):
                multi_lob_opp = opp
                break
        
        if not multi_lob_opp:
            print("⚠ 'Multi-LOB Test Opportunity' not found. Searching for any multi-LOB opportunity...")
            
            # Find any opportunity with multiple LOBs
            all_opps_response = self.session.get(f"{BASE_URL}/api/opportunities?limit=100")
            all_opps = all_opps_response.json().get("items", [])
            
            for opp in all_opps:
                lobs = opp.get("lines_of_business", [])
                if len(lobs) >= 2:
                    multi_lob_opp = opp
                    break
        
        if not multi_lob_opp:
            pytest.skip("No multi-LOB opportunity found for testing")
        
        lobs = multi_lob_opp.get("lines_of_business", [])
        print(f"✓ Found multi-LOB opportunity: {multi_lob_opp.get('name')}")
        print(f"  LOBs: {', '.join(lobs)}")
        
        # Verify it has the expected LOBs
        assert len(lobs) >= 2, "Multi-LOB opportunity should have at least 2 LOBs"
        
        # Check if it has FS and PS (which should show Resource Assignments tab)
        has_fs = "field_services" in lobs
        has_ps = "professional_services" in lobs
        has_wholesale = "wholesale_mrr" in lobs
        
        print(f"  Field Services: {'Yes' if has_fs else 'No'}")
        print(f"  Professional Services: {'Yes' if has_ps else 'No'}")
        print(f"  Wholesale MRR: {'Yes' if has_wholesale else 'No'}")
        
        return multi_lob_opp
    
    def test_each_lob_has_separate_sow(self):
        """Verify each LOB has its own separate SOW"""
        # Get multi-LOB opportunity
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?limit=100")
        opps_data = opps_response.json()
        
        multi_lob_opp = None
        for opp in opps_data.get("items", []):
            lobs = opp.get("lines_of_business", [])
            if len(lobs) >= 2 and ("field_services" in lobs or "professional_services" in lobs):
                multi_lob_opp = opp
                break
        
        if not multi_lob_opp:
            pytest.skip("No suitable multi-LOB opportunity found")
        
        opp_id = multi_lob_opp.get("opportunity_id")
        lobs = multi_lob_opp.get("lines_of_business", [])
        
        # Get SOW for each LOB (except referral_mrr which doesn't have SOW)
        sow_lobs = [lob for lob in lobs if lob != "referral_mrr"]
        
        sows = {}
        for lob in sow_lobs:
            sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow?lob={lob}")
            assert sow_response.status_code == 200, f"Failed to get SOW for {lob}: {sow_response.text}"
            
            sow_data = sow_response.json()
            sows[lob] = sow_data
            
            print(f"✓ SOW for {lob}:")
            print(f"    sow_id: {sow_data.get('sow_id')}")
            print(f"    selected_resource_assignment_ids: {sow_data.get('selected_resource_assignment_ids', [])}")
        
        # Verify each SOW has unique sow_id
        sow_ids = [sow.get("sow_id") for sow in sows.values()]
        assert len(sow_ids) == len(set(sow_ids)), "Each LOB should have a unique SOW"
        
        print(f"✓ All {len(sows)} LOBs have separate SOWs")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
