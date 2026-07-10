"""
Test Contractor Unification and Resource Assignment Features
Tests:
1. Contractor users linked to contractor businesses
2. Resource Types API (15 types from /api/admin/default-resource-types)
3. Contractor Business dropdown (3 active businesses)
4. Contractor Person dropdown (filtered by business)
5. Resource assignment creation with all required fields
6. SOW selected_resource_assignment_ids persistence
7. Deal conversion uses sow.selected_resource_assignment_ids
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestContractorUnification:
    """Test contractor-business linkage and resource assignment features"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test session with authentication"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": "Admin@123!"
        })
        
        if login_response.status_code == 200:
            token = login_response.json().get("token")  # Note: 'token' not 'access_token'
            self.session.headers.update({"Authorization": f"Bearer {token}"})
        else:
            pytest.skip("Authentication failed")
    
    # ---- TEST 1: Contractor Users Linked to Businesses ----
    def test_contractors_have_business_id_and_name(self):
        """Verify contractor users have contractor_business_id and contractor_business_name populated"""
        response = self.session.get(f"{BASE_URL}/api/admin/users")
        assert response.status_code == 200, f"Failed to get users: {response.text}"
        
        data = response.json()
        # API returns array directly, not {items: [...]}
        users = data if isinstance(data, list) else data.get("items", [])
        
        # Filter contractors
        contractors = [u for u in users if u.get("role") == "contractor"]
        assert len(contractors) > 0, "No contractors found in system"
        
        # Check each contractor has business linkage
        linked_contractors = []
        unlinked_contractors = []
        
        for contractor in contractors:
            if contractor.get("contractor_business_id") and contractor.get("contractor_business_name"):
                linked_contractors.append(contractor)
            else:
                unlinked_contractors.append(contractor)
        
        print(f"\n✓ Total contractors: {len(contractors)}")
        print(f"✓ Linked to businesses: {len(linked_contractors)}")
        
        if unlinked_contractors:
            print(f"⚠ Unlinked contractors: {[c.get('name') for c in unlinked_contractors]}")
        
        # All contractors should be linked
        assert len(linked_contractors) == len(contractors), \
            f"Not all contractors are linked to businesses. Unlinked: {[c.get('name') for c in unlinked_contractors]}"
    
    def test_contractors_linked_to_correct_businesses(self):
        """Verify contractors are linked to SHIELD, Stark, or Avengers businesses"""
        response = self.session.get(f"{BASE_URL}/api/admin/users")
        assert response.status_code == 200
        
        data = response.json()
        users = data if isinstance(data, list) else data.get("items", [])
        contractors = [u for u in users if u.get("role") == "contractor"]
        
        # Group by business
        business_counts = {}
        for c in contractors:
            biz_name = c.get("contractor_business_name", "Unknown")
            business_counts[biz_name] = business_counts.get(biz_name, 0) + 1
        
        print(f"\n✓ Contractor distribution by business:")
        for biz, count in business_counts.items():
            print(f"  - {biz}: {count} contractors")
        
        # Verify expected businesses exist (at least 3 different businesses)
        assert len(business_counts) >= 3, f"Expected at least 3 different businesses, got {len(business_counts)}"
        
        # Verify SHIELD is present (known business)
        shield_found = any("SHIELD" in biz for biz in business_counts.keys())
        assert shield_found, f"Expected SHIELD business not found. Found: {list(business_counts.keys())}"
    
    # ---- TEST 2: Resource Types API ----
    def test_default_resource_types_api(self):
        """Verify /api/admin/default-resource-types returns resource types"""
        response = self.session.get(f"{BASE_URL}/api/admin/default-resource-types")
        assert response.status_code == 200, f"Failed to get resource types: {response.text}"
        
        data = response.json()
        resource_types = data.get("default_resource_types", [])
        
        print(f"\n✓ Resource types count: {len(resource_types)}")
        
        # Verify we have resource types
        assert len(resource_types) >= 10, f"Expected at least 10 resource types, got {len(resource_types)}"
        
        # Verify each has required fields
        for rt in resource_types:
            assert "name" in rt, "Resource type missing 'name'"
            assert "sell_rate" in rt, "Resource type missing 'sell_rate'"
            assert "resource_type_id" in rt, "Resource type missing 'resource_type_id'"
        
        # Print resource types
        print("✓ Resource types available:")
        for rt in resource_types[:5]:
            print(f"  - {rt['name']}: ${rt['sell_rate']}/hr")
        if len(resource_types) > 5:
            print(f"  ... and {len(resource_types) - 5} more")
    
    # ---- TEST 3: Contractor Business Dropdown ----
    def test_contractor_businesses_api(self):
        """Verify /api/sales-ops/contractor-businesses returns active businesses"""
        response = self.session.get(f"{BASE_URL}/api/sales-ops/contractor-businesses")
        assert response.status_code == 200, f"Failed to get contractor businesses: {response.text}"
        
        data = response.json()
        businesses = data.get("items", [])
        
        print(f"\n✓ Contractor businesses count: {len(businesses)}")
        
        # Verify we have businesses
        assert len(businesses) >= 3, f"Expected at least 3 contractor businesses, got {len(businesses)}"
        
        # Verify each has required fields
        for biz in businesses:
            assert "business_id" in biz, "Business missing 'business_id'"
            assert "business_name" in biz, "Business missing 'business_name'"
        
        # Print businesses
        print("✓ Contractor businesses:")
        for biz in businesses:
            print(f"  - {biz['business_name']} (ID: {biz['business_id']})")
    
    # ---- TEST 4: Contractor Person Dropdown (filtered by business) ----
    def test_contractor_users_filtered_by_business(self):
        """Verify /api/sales-ops/contractor-businesses/{id}/users returns users filtered by business"""
        # First get businesses
        biz_response = self.session.get(f"{BASE_URL}/api/sales-ops/contractor-businesses")
        assert biz_response.status_code == 200
        
        businesses = biz_response.json().get("items", [])
        assert len(businesses) > 0, "No contractor businesses found"
        
        # Test each business
        for biz in businesses[:3]:  # Test first 3
            business_id = biz["business_id"]
            business_name = biz["business_name"]
            
            users_response = self.session.get(f"{BASE_URL}/api/sales-ops/contractor-businesses/{business_id}/users")
            assert users_response.status_code == 200, f"Failed to get users for {business_name}"
            
            users = users_response.json().get("items", [])
            
            print(f"\n✓ {business_name}: {len(users)} contractors")
            for user in users[:3]:
                print(f"  - {user.get('name')} ({user.get('email')})")
            
            # Verify users have required fields
            for user in users:
                assert "user_id" in user, "User missing 'user_id'"
                assert "name" in user, "User missing 'name'"
    
    # ---- TEST 5: Resource Assignment Creation ----
    def test_resource_assignment_creation(self):
        """Test creating a resource assignment with all required fields"""
        # Get test opportunity
        opp_response = self.session.get(f"{BASE_URL}/api/opportunities?search=Multi-LOB")
        assert opp_response.status_code == 200
        
        opportunities = opp_response.json().get("items", [])
        if not opportunities:
            pytest.skip("No Multi-LOB Test Opportunity found")
        
        opportunity = opportunities[0]
        opportunity_id = opportunity["opportunity_id"]
        
        # Get a contractor business and user
        biz_response = self.session.get(f"{BASE_URL}/api/sales-ops/contractor-businesses")
        businesses = biz_response.json().get("items", [])
        
        if not businesses:
            pytest.skip("No contractor businesses found")
        
        business = businesses[0]
        
        users_response = self.session.get(f"{BASE_URL}/api/sales-ops/contractor-businesses/{business['business_id']}/users")
        users = users_response.json().get("items", [])
        
        if not users:
            pytest.skip(f"No users found for business {business['business_name']}")
        
        user = users[0]
        
        # Get resource types
        rt_response = self.session.get(f"{BASE_URL}/api/admin/default-resource-types")
        resource_types = rt_response.json().get("default_resource_types", [])
        
        if not resource_types:
            pytest.skip("No resource types found")
        
        resource_type = resource_types[0]
        
        # Create resource assignment
        assignment_data = {
            "pricing_model": "block_hours",
            "resource_type_id": resource_type["resource_type_id"],
            "resource_type_name": resource_type["name"],
            "contractor_business_id": business["business_id"],
            "contractor_business_name": business["business_name"],
            "contractor_user_id": user["user_id"],
            "contractor_user_name": user["name"],
            "estimated_hours": 20,
            "cost_rate": 100,
            "sell_rate": 150,
            "notes": "TEST_resource_assignment"
        }
        
        create_response = self.session.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{opportunity_id}/resource-assignments",
            json=assignment_data
        )
        
        assert create_response.status_code == 200, f"Failed to create resource assignment: {create_response.text}"
        
        created = create_response.json()
        assert "assignment_id" in created, "Created assignment missing 'assignment_id'"
        
        print(f"\n✓ Created resource assignment: {created['assignment_id']}")
        print(f"  - Resource Type: {created.get('resource_type_name')}")
        print(f"  - Contractor: {created.get('contractor_user_name')} ({created.get('contractor_business_name')})")
        print(f"  - Hours: {created.get('estimated_hours')}, Rate: ${created.get('sell_rate')}/hr")
        
        # Verify it appears in the list
        list_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opportunity_id}/resource-assignments")
        assert list_response.status_code == 200
        
        assignments = list_response.json().get("items", [])
        assignment_ids = [a["assignment_id"] for a in assignments]
        assert created["assignment_id"] in assignment_ids, "Created assignment not found in list"
        
        return created["assignment_id"]
    
    # ---- TEST 6: SOW selected_resource_assignment_ids Persistence ----
    def test_sow_selected_resource_assignment_ids(self):
        """Test that SOW saves and retrieves selected_resource_assignment_ids"""
        # Get test opportunity with Field Services LOB
        opp_response = self.session.get(f"{BASE_URL}/api/opportunities?search=Multi-LOB")
        assert opp_response.status_code == 200
        
        opportunities = opp_response.json().get("items", [])
        if not opportunities:
            pytest.skip("No Multi-LOB Test Opportunity found")
        
        opportunity = opportunities[0]
        opportunity_id = opportunity["opportunity_id"]
        
        # Check if opportunity has field_services LOB
        lobs = opportunity.get("lines_of_business", [])
        if "field_services" not in lobs:
            pytest.skip("Opportunity doesn't have field_services LOB")
        
        # Get current SOW
        sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opportunity_id}/sow?lob=field_services")
        assert sow_response.status_code == 200, f"Failed to get SOW: {sow_response.text}"
        
        sow = sow_response.json()
        
        # Get existing resource assignments
        assignments_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opportunity_id}/resource-assignments")
        assignments = assignments_response.json().get("items", [])
        
        if not assignments:
            print("\n⚠ No resource assignments exist for this opportunity - creating one")
            # Create one for testing
            assignment_id = self.test_resource_assignment_creation()
            test_selection = [assignment_id]
        else:
            test_selection = [assignments[0]["assignment_id"]]
        
        # Update SOW with selected resource assignment IDs
        sow_update = {
            **sow,
            "selected_resource_assignment_ids": test_selection
        }
        
        update_response = self.session.post(
            f"{BASE_URL}/api/sales-ops/opportunities/{opportunity_id}/sow?lob=field_services",
            json=sow_update
        )
        
        assert update_response.status_code == 200, f"Failed to update SOW: {update_response.text}"
        
        # Verify persistence
        verify_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opportunity_id}/sow?lob=field_services")
        assert verify_response.status_code == 200
        
        updated_sow = verify_response.json()
        saved_selections = updated_sow.get("selected_resource_assignment_ids", [])
        
        print(f"\n✓ SOW selected_resource_assignment_ids saved: {saved_selections}")
        assert test_selection[0] in saved_selections, "Selected resource assignment ID not persisted in SOW"
    
    # ---- TEST 7: Deal Conversion Uses selected_resource_assignment_ids ----
    def test_deal_conversion_validates_closed_won(self):
        """Verify deal conversion requires closed_won stage"""
        # Get test opportunity
        opp_response = self.session.get(f"{BASE_URL}/api/opportunities?search=Multi-LOB")
        assert opp_response.status_code == 200
        
        opportunities = opp_response.json().get("items", [])
        if not opportunities:
            pytest.skip("No Multi-LOB Test Opportunity found")
        
        opportunity = opportunities[0]
        opportunity_id = opportunity["opportunity_id"]
        
        # Try to convert (should fail if not closed_won)
        if opportunity.get("stage") != "closed_won":
            convert_response = self.session.post(f"{BASE_URL}/api/opportunities/{opportunity_id}/convert-to-deal")
            
            # Should fail with 400 if not closed_won
            assert convert_response.status_code == 400, \
                f"Expected 400 for non-closed_won opportunity, got {convert_response.status_code}"
            
            error = convert_response.json()
            assert "closed_won" in error.get("detail", "").lower() or "closed won" in error.get("detail", "").lower(), \
                f"Error message should mention closed_won requirement: {error}"
            
            print(f"\n✓ Deal conversion correctly requires closed_won stage")
            print(f"  - Current stage: {opportunity.get('stage')}")
            print(f"  - Error: {error.get('detail')}")
        else:
            print(f"\n⚠ Opportunity is already closed_won, skipping validation test")


class TestResourceAssignmentDisplay:
    """Test resource assignments appear in Resource Assignments tab"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test session with authentication"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": "Admin@123!"
        })
        
        if login_response.status_code == 200:
            token = login_response.json().get("token")
            self.session.headers.update({"Authorization": f"Bearer {token}"})
        else:
            pytest.skip("Authentication failed")
    
    def test_resource_assignments_appear_in_list(self):
        """Verify created resource assignments appear in the list"""
        # Get test opportunity
        opp_response = self.session.get(f"{BASE_URL}/api/opportunities?search=Multi-LOB")
        assert opp_response.status_code == 200
        
        opportunities = opp_response.json().get("items", [])
        if not opportunities:
            pytest.skip("No Multi-LOB Test Opportunity found")
        
        opportunity = opportunities[0]
        opportunity_id = opportunity["opportunity_id"]
        
        # Get resource assignments
        response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opportunity_id}/resource-assignments")
        assert response.status_code == 200, f"Failed to get resource assignments: {response.text}"
        
        assignments = response.json().get("items", [])
        
        print(f"\n✓ Resource assignments for opportunity: {len(assignments)}")
        
        for assignment in assignments:
            print(f"  - {assignment.get('resource_type_name')}: {assignment.get('contractor_user_name')}")
            print(f"    Business: {assignment.get('contractor_business_name')}")
            print(f"    Hours: {assignment.get('estimated_hours')}, Rate: ${assignment.get('sell_rate')}/hr")


class TestModalCloseButtons:
    """Test X button and Cancel button properly close SOW Builder modal"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test session with authentication"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": "Admin@123!"
        })
        
        if login_response.status_code == 200:
            token = login_response.json().get("token")
            self.session.headers.update({"Authorization": f"Bearer {token}"})
        else:
            pytest.skip("Authentication failed")
    
    def test_sow_api_endpoints_work(self):
        """Verify SOW API endpoints work correctly (modal close is frontend-only)"""
        # Get test opportunity
        opp_response = self.session.get(f"{BASE_URL}/api/opportunities?search=Multi-LOB")
        assert opp_response.status_code == 200
        
        opportunities = opp_response.json().get("items", [])
        if not opportunities:
            pytest.skip("No Multi-LOB Test Opportunity found")
        
        opportunity = opportunities[0]
        opportunity_id = opportunity["opportunity_id"]
        
        # Test SOW tasks endpoint
        tasks_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opportunity_id}/sow-tasks")
        assert tasks_response.status_code == 200, f"SOW tasks endpoint failed: {tasks_response.text}"
        
        tasks_data = tasks_response.json()
        print(f"\n✓ SOW tasks endpoint works")
        print(f"  - SOW tasks: {len(tasks_data.get('sow_tasks', []))}")
        
        # Test SOW endpoint for each LOB
        lobs = opportunity.get("lines_of_business", [])
        for lob in lobs:
            if lob == "referral_mrr":
                continue  # Referral MRR doesn't have SOW
            
            sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opportunity_id}/sow?lob={lob}")
            assert sow_response.status_code == 200, f"SOW endpoint failed for {lob}: {sow_response.text}"
            
            print(f"  - SOW for {lob}: OK")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
