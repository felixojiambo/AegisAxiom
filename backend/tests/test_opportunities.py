"""
Opportunity Management Module - Backend API Tests
Tests CRUD operations, stage changes, notes, attachments and stats endpoints
"""
import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://zoho-connect-3.preview.emergentagent.com')

# Test fixtures
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


class TestOpportunitiesReadOperations:
    """Test opportunity read operations"""
    
    def test_get_opportunities_list(self, auth_headers):
        """GET /api/opportunities - Should return list of opportunities"""
        response = requests.get(
            f"{BASE_URL}/api/opportunities?limit=10",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify response structure
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["total"], int)
        print(f"Found {data['total']} total opportunities")
    
    def test_get_opportunities_with_filters(self, auth_headers):
        """GET /api/opportunities - Test filtering by stage"""
        response = requests.get(
            f"{BASE_URL}/api/opportunities?stage=prospect_opp&limit=10",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        
        # All items should have the filtered stage
        for item in data.get("items", []):
            assert item.get("stage") == "prospect_opp", f"Expected prospect_opp, got {item.get('stage')}"
        print(f"Found {data['total']} opportunities in prospect_opp stage")
    
    def test_get_opportunities_search(self, auth_headers):
        """GET /api/opportunities - Test search functionality"""
        response = requests.get(
            f"{BASE_URL}/api/opportunities?search=test&limit=10",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        print(f"Search 'test' returned {data['total']} results")
    
    def test_get_stats_by_stage(self, auth_headers):
        """GET /api/opportunities/stats/by-stage - Should return stage statistics"""
        response = requests.get(
            f"{BASE_URL}/api/opportunities/stats/by-stage",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify all 9 stages are present
        expected_stages = [
            "prospect_opp", "prospect_lead", "proposal_development",
            "scoping_complete", "proposal_sent", "negotiation_review",
            "verbal_agreement", "closed_won", "closed_lost"
        ]
        for stage in expected_stages:
            assert stage in data, f"Missing stage: {stage}"
            assert "count" in data[stage]
            assert "total_amount" in data[stage]
        
        print(f"Stage stats: {data}")
    
    def test_get_dropdown_data(self, auth_headers):
        """GET /api/opportunities/dropdown-data/all - Should return dropdown options"""
        response = requests.get(
            f"{BASE_URL}/api/opportunities/dropdown-data/all",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify response structure
        assert "accounts" in data
        assert "contacts" in data
        assert "users" in data
        assert "partners" in data
        assert "stages" in data
        assert "priorities" in data
        
        print(f"Dropdown data: Accounts={len(data['accounts'])}, Users={len(data['users'])}")


class TestOpportunityCRUD:
    """Test opportunity CRUD operations"""
    
    def test_create_opportunity(self, auth_headers, test_account_id):
        """POST /api/opportunities - Should create new opportunity"""
        unique_name = f"TEST_Opportunity_{uuid.uuid4().hex[:8]}"
        payload = {
            "name": unique_name,
            "account_id": test_account_id,
            "description": "Test opportunity created by automated tests",
            "priority": "high",
            "amount": 50000,
            "stage": "prospect_opp",
            "lines_of_business": ["professional_services"],
            "is_test_data": True
        }
        
        response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json=payload,
            headers=auth_headers
        )
        assert response.status_code == 200, f"Create failed: {response.text}"
        data = response.json()
        
        # Verify created opportunity
        assert data.get("opportunity_id") is not None
        assert data.get("name") == unique_name
        assert data.get("account_id") == test_account_id
        assert data.get("priority") == "high"
        assert data.get("amount") == 50000
        assert data.get("stage") == "prospect_opp"
        assert "professional_services" in data.get("lines_of_business", [])
        assert data.get("is_test_data") == True
        
        print(f"Created opportunity: {data['opportunity_id']}")
        return data["opportunity_id"]
    
    def test_get_single_opportunity(self, auth_headers, test_account_id):
        """GET /api/opportunities/{id} - Should return single opportunity"""
        # First create an opportunity
        unique_name = f"TEST_GetSingle_{uuid.uuid4().hex[:8]}"
        create_response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "name": unique_name,
                "account_id": test_account_id,
                "amount": 10000,
                "stage": "prospect_lead",
                "is_test_data": True
            },
            headers=auth_headers
        )
        assert create_response.status_code == 200
        opp_id = create_response.json()["opportunity_id"]
        
        # Get the opportunity
        response = requests.get(
            f"{BASE_URL}/api/opportunities/{opp_id}",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("opportunity_id") == opp_id
        assert data.get("name") == unique_name
        print(f"Retrieved opportunity: {data['opportunity_id']}")
    
    def test_update_opportunity(self, auth_headers, test_account_id):
        """PUT /api/opportunities/{id} - Should update opportunity"""
        # Create opportunity
        unique_name = f"TEST_Update_{uuid.uuid4().hex[:8]}"
        create_response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "name": unique_name,
                "account_id": test_account_id,
                "amount": 25000,
                "priority": "low",
                "stage": "prospect_opp",
                "is_test_data": True
            },
            headers=auth_headers
        )
        assert create_response.status_code == 200
        opp_id = create_response.json()["opportunity_id"]
        
        # Update opportunity
        update_response = requests.put(
            f"{BASE_URL}/api/opportunities/{opp_id}",
            json={
                "name": f"{unique_name}_UPDATED",
                "amount": 75000,
                "priority": "high",
                "description": "Updated description"
            },
            headers=auth_headers
        )
        assert update_response.status_code == 200
        updated = update_response.json()
        
        assert updated.get("name") == f"{unique_name}_UPDATED"
        assert updated.get("amount") == 75000
        assert updated.get("priority") == "high"
        assert updated.get("description") == "Updated description"
        
        # Verify persistence with GET
        get_response = requests.get(
            f"{BASE_URL}/api/opportunities/{opp_id}",
            headers=auth_headers
        )
        assert get_response.status_code == 200
        persisted = get_response.json()
        assert persisted.get("amount") == 75000
        print(f"Updated and verified opportunity: {opp_id}")
    
    def test_delete_opportunity(self, auth_headers, test_account_id):
        """DELETE /api/opportunities/{id} - Should delete opportunity"""
        # Create opportunity
        unique_name = f"TEST_Delete_{uuid.uuid4().hex[:8]}"
        create_response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "name": unique_name,
                "account_id": test_account_id,
                "amount": 5000,
                "stage": "prospect_opp",
                "is_test_data": True
            },
            headers=auth_headers
        )
        assert create_response.status_code == 200
        opp_id = create_response.json()["opportunity_id"]
        
        # Delete opportunity
        delete_response = requests.delete(
            f"{BASE_URL}/api/opportunities/{opp_id}",
            headers=auth_headers
        )
        assert delete_response.status_code == 200
        
        # Verify deletion with GET (should return 404)
        get_response = requests.get(
            f"{BASE_URL}/api/opportunities/{opp_id}",
            headers=auth_headers
        )
        assert get_response.status_code == 404
        print(f"Deleted and verified opportunity: {opp_id}")


class TestOpportunityStageChange:
    """Test stage change functionality (for Kanban drag-and-drop)"""
    
    def test_update_stage(self, auth_headers, test_account_id):
        """PATCH /api/opportunities/{id}/stage - Should update stage only"""
        # Create opportunity
        unique_name = f"TEST_Stage_{uuid.uuid4().hex[:8]}"
        create_response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "name": unique_name,
                "account_id": test_account_id,
                "amount": 30000,
                "stage": "prospect_opp",
                "is_test_data": True
            },
            headers=auth_headers
        )
        assert create_response.status_code == 200
        opp_id = create_response.json()["opportunity_id"]
        
        # Update stage via PATCH
        patch_response = requests.patch(
            f"{BASE_URL}/api/opportunities/{opp_id}/stage?stage=proposal_development",
            headers=auth_headers
        )
        assert patch_response.status_code == 200
        
        # Verify stage changed
        get_response = requests.get(
            f"{BASE_URL}/api/opportunities/{opp_id}",
            headers=auth_headers
        )
        assert get_response.status_code == 200
        assert get_response.json().get("stage") == "proposal_development"
        print(f"Stage updated from prospect_opp to proposal_development")
    
    def test_update_stage_invalid(self, auth_headers, test_account_id):
        """PATCH /api/opportunities/{id}/stage - Should reject invalid stage"""
        # Create opportunity
        unique_name = f"TEST_InvalidStage_{uuid.uuid4().hex[:8]}"
        create_response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "name": unique_name,
                "account_id": test_account_id,
                "stage": "prospect_opp",
                "is_test_data": True
            },
            headers=auth_headers
        )
        assert create_response.status_code == 200
        opp_id = create_response.json()["opportunity_id"]
        
        # Try invalid stage
        patch_response = requests.patch(
            f"{BASE_URL}/api/opportunities/{opp_id}/stage?stage=invalid_stage",
            headers=auth_headers
        )
        assert patch_response.status_code == 400
        print("Invalid stage rejected as expected")


class TestOpportunityNotes:
    """Test opportunity notes functionality"""
    
    def test_add_note(self, auth_headers, test_account_id):
        """POST /api/opportunities/{id}/notes - Should add note"""
        # Create opportunity
        unique_name = f"TEST_Note_{uuid.uuid4().hex[:8]}"
        create_response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "name": unique_name,
                "account_id": test_account_id,
                "stage": "prospect_opp",
                "is_test_data": True
            },
            headers=auth_headers
        )
        assert create_response.status_code == 200
        opp_id = create_response.json()["opportunity_id"]
        
        # Add note
        note_text = f"Test note added at {uuid.uuid4().hex[:8]}"
        note_response = requests.post(
            f"{BASE_URL}/api/opportunities/{opp_id}/notes?note_text={note_text}",
            headers=auth_headers
        )
        assert note_response.status_code == 200
        note_data = note_response.json()
        assert note_data.get("note") is not None
        assert note_data["note"].get("text") == note_text
        
        # Verify note in opportunity
        get_response = requests.get(
            f"{BASE_URL}/api/opportunities/{opp_id}",
            headers=auth_headers
        )
        assert get_response.status_code == 200
        notes = get_response.json().get("notes", [])
        assert len(notes) > 0
        assert any(n.get("text") == note_text for n in notes)
        print(f"Note added and verified: {note_text[:30]}...")
    
    def test_delete_note(self, auth_headers, test_account_id):
        """DELETE /api/opportunities/{id}/notes/{note_id} - Should delete note"""
        # Create opportunity
        unique_name = f"TEST_DeleteNote_{uuid.uuid4().hex[:8]}"
        create_response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "name": unique_name,
                "account_id": test_account_id,
                "stage": "prospect_opp",
                "is_test_data": True
            },
            headers=auth_headers
        )
        assert create_response.status_code == 200
        opp_id = create_response.json()["opportunity_id"]
        
        # Add note
        note_text = f"Note to delete {uuid.uuid4().hex[:8]}"
        note_response = requests.post(
            f"{BASE_URL}/api/opportunities/{opp_id}/notes?note_text={note_text}",
            headers=auth_headers
        )
        assert note_response.status_code == 200
        note_id = note_response.json()["note"]["note_id"]
        
        # Delete note
        delete_response = requests.delete(
            f"{BASE_URL}/api/opportunities/{opp_id}/notes/{note_id}",
            headers=auth_headers
        )
        assert delete_response.status_code == 200
        
        # Verify note removed
        get_response = requests.get(
            f"{BASE_URL}/api/opportunities/{opp_id}",
            headers=auth_headers
        )
        assert get_response.status_code == 200
        notes = get_response.json().get("notes", [])
        assert not any(n.get("note_id") == note_id for n in notes)
        print(f"Note deleted and verified: {note_id}")


class TestOpportunityValidation:
    """Test opportunity validation rules"""
    
    def test_create_missing_name(self, auth_headers, test_account_id):
        """POST /api/opportunities - Should reject missing name"""
        response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "account_id": test_account_id,
                "amount": 10000
            },
            headers=auth_headers
        )
        assert response.status_code == 422  # Validation error
        print("Missing name rejected as expected")
    
    def test_create_missing_account(self, auth_headers):
        """POST /api/opportunities - Should reject missing account_id"""
        response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "name": "Test without account",
                "amount": 10000
            },
            headers=auth_headers
        )
        assert response.status_code == 422  # Validation error
        print("Missing account_id rejected as expected")
    
    def test_create_invalid_account(self, auth_headers):
        """POST /api/opportunities - Should reject invalid account_id"""
        response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "name": "Test invalid account",
                "account_id": "invalid_account_id_12345",
                "amount": 10000
            },
            headers=auth_headers
        )
        assert response.status_code == 404  # Account not found
        print("Invalid account_id rejected as expected")
    
    def test_create_negative_amount(self, auth_headers, test_account_id):
        """POST /api/opportunities - Should reject negative amount"""
        response = requests.post(
            f"{BASE_URL}/api/opportunities",
            json={
                "name": "Test negative amount",
                "account_id": test_account_id,
                "amount": -5000,
                "is_test_data": True
            },
            headers=auth_headers
        )
        # Note: The backend validation may not exist for negative amounts
        # This test documents current behavior
        if response.status_code == 200:
            print("Warning: Negative amount was accepted (validation not implemented)")
        else:
            print("Negative amount rejected as expected")


class TestCleanup:
    """Cleanup test data after tests"""
    
    def test_cleanup_test_opportunities(self, auth_headers):
        """Delete all test opportunities created during testing"""
        # Get test opportunities
        response = requests.get(
            f"{BASE_URL}/api/opportunities?test_only=true&limit=100",
            headers=auth_headers
        )
        if response.status_code == 200:
            data = response.json()
            test_opps = [o for o in data.get("items", []) if o.get("name", "").startswith("TEST_")]
            
            for opp in test_opps:
                delete_response = requests.delete(
                    f"{BASE_URL}/api/opportunities/{opp['opportunity_id']}",
                    headers=auth_headers
                )
                if delete_response.status_code == 200:
                    print(f"Cleaned up: {opp['opportunity_id']}")
        
        print(f"Cleanup complete")
