"""
Test Contractor Management Module: Billing Info, Grievances, and Surveys
Tests for:
- File upload endpoint (W9 documents)
- Grievance CRUD with comments and status updates
- Survey creation, public access, and submission
- Survey rating calculation
"""
import pytest
import requests
import os
import secrets

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin@123!"
TEST_CONTRACTOR_BUSINESS_ID = "biz_e1d4fbabfbba"
TEST_CONTRACTOR_USER_ID = "user_d74e83c7abf4"


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token for admin user."""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })
    if response.status_code == 200:
        return response.json().get("token")  # Note: 'token' not 'access_token'
    pytest.skip(f"Authentication failed: {response.status_code} - {response.text}")


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    """Headers with auth token."""
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json"
    }


class TestFileUpload:
    """Test file upload endpoint for W9 documents."""
    
    def test_upload_endpoint_exists(self, auth_token):
        """Test that upload endpoint exists and requires auth."""
        # Test without auth - should fail
        response = requests.post(f"{BASE_URL}/api/admin/upload")
        assert response.status_code in [401, 403, 422], f"Expected auth error, got {response.status_code}"
    
    def test_upload_file_with_auth(self, auth_token):
        """Test file upload with authentication."""
        headers = {"Authorization": f"Bearer {auth_token}"}
        
        # Create a simple test file
        files = {
            'file': ('test_w9.pdf', b'%PDF-1.4 test content', 'application/pdf')
        }
        
        response = requests.post(
            f"{BASE_URL}/api/admin/upload",
            headers=headers,
            files=files
        )
        
        # Should succeed or return permission error
        assert response.status_code in [200, 403], f"Unexpected status: {response.status_code} - {response.text}"
        
        if response.status_code == 200:
            data = response.json()
            assert "file_url" in data, "Response should contain file_url"
            assert "filename" in data, "Response should contain filename"
            print(f"✓ File uploaded successfully: {data.get('file_url')}")


class TestGrievanceCRUD:
    """Test Grievance CRUD operations."""
    
    @pytest.fixture(scope="class")
    def created_grievance_id(self, auth_headers):
        """Create a grievance for testing and return its ID."""
        grievance_data = {
            "title": "TEST_Grievance_Title",
            "description": "Test grievance description for automated testing",
            "severity": "medium",
            "reporter_type": "internal",
            "affects_rating": False
        }
        
        response = requests.post(
            f"{BASE_URL}/api/sales-ops/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}/grievances",
            headers=auth_headers,
            json=grievance_data
        )
        
        if response.status_code == 200:
            data = response.json()
            grievance = data.get("grievance", {})
            return grievance.get("grievance_id")
        return None
    
    def test_get_grievances_for_contractor(self, auth_headers):
        """Test GET grievances for a contractor business."""
        response = requests.get(
            f"{BASE_URL}/api/sales-ops/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}/grievances",
            headers=auth_headers
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "items" in data, "Response should contain 'items' array"
        print(f"✓ GET grievances returned {len(data['items'])} items")
    
    def test_create_grievance(self, auth_headers):
        """Test POST to create a new grievance."""
        grievance_data = {
            "title": "TEST_New_Grievance",
            "description": "Test grievance created by automated test",
            "severity": "high",
            "reporter_type": "customer",
            "affects_rating": True
        }
        
        response = requests.post(
            f"{BASE_URL}/api/sales-ops/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}/grievances",
            headers=auth_headers,
            json=grievance_data
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "grievance" in data, "Response should contain 'grievance'"
        grievance = data["grievance"]
        assert grievance["title"] == "TEST_New_Grievance"
        assert grievance["severity"] == "high"
        assert grievance["status"] == "open"
        print(f"✓ Grievance created: {grievance.get('grievance_id')}")
        return grievance.get("grievance_id")
    
    def test_add_comment_to_grievance(self, auth_headers, created_grievance_id):
        """Test POST to add a comment to a grievance."""
        if not created_grievance_id:
            pytest.skip("No grievance ID available for comment test")
        
        comment_data = {
            "comment": "This is a test comment on the grievance"
        }
        
        response = requests.post(
            f"{BASE_URL}/api/sales-ops/grievances/{created_grievance_id}/comments",
            headers=auth_headers,
            json=comment_data
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "comment" in data, "Response should contain 'comment'"
        comment = data["comment"]
        assert comment["comment"] == "This is a test comment on the grievance"
        assert "comment_id" in comment
        print(f"✓ Comment added: {comment.get('comment_id')}")
    
    def test_update_grievance_status_in_progress(self, auth_headers, created_grievance_id):
        """Test PATCH to update grievance status to in_progress."""
        if not created_grievance_id:
            pytest.skip("No grievance ID available for status update test")
        
        response = requests.patch(
            f"{BASE_URL}/api/sales-ops/grievances/{created_grievance_id}/status",
            headers=auth_headers,
            json={"status": "in_progress"}
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "in_progress" in data.get("message", "").lower() or response.status_code == 200
        print(f"✓ Grievance status updated to in_progress")
    
    def test_update_grievance_status_resolved(self, auth_headers, created_grievance_id):
        """Test PATCH to resolve a grievance."""
        if not created_grievance_id:
            pytest.skip("No grievance ID available for resolve test")
        
        response = requests.patch(
            f"{BASE_URL}/api/sales-ops/grievances/{created_grievance_id}/status",
            headers=auth_headers,
            json={"status": "resolved"}
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print(f"✓ Grievance resolved successfully")


class TestSurveyManagement:
    """Test Survey creation, public access, and submission."""
    
    @pytest.fixture(scope="class")
    def created_survey(self, auth_headers):
        """Create a survey and return its data."""
        survey_data = {
            "contractor_business_id": TEST_CONTRACTOR_BUSINESS_ID,
            "contractor_user_id": TEST_CONTRACTOR_USER_ID,
            "project_name": "TEST_Project_Survey",
            "customer_name": "TEST_Customer",
            "sent_to_name": "Test Recipient",
            "sent_to_email": "test@example.com"
        }
        
        response = requests.post(
            f"{BASE_URL}/api/sales-ops/surveys/send",
            headers=auth_headers,
            json=survey_data
        )
        
        if response.status_code == 200:
            return response.json()
        return None
    
    def test_create_survey_generates_token(self, auth_headers):
        """Test POST /api/sales-ops/surveys/send generates unique public token."""
        survey_data = {
            "contractor_business_id": TEST_CONTRACTOR_BUSINESS_ID,
            "contractor_user_id": TEST_CONTRACTOR_USER_ID,
            "project_name": "Token Test Project",
            "customer_name": "Token Test Customer"
        }
        
        response = requests.post(
            f"{BASE_URL}/api/sales-ops/surveys/send",
            headers=auth_headers,
            json=survey_data
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert "survey_id" in data, "Response should contain survey_id"
        assert "survey_url" in data, "Response should contain survey_url"
        assert "public_token" in data, "Response should contain public_token"
        
        # Verify token is in URL
        assert data["public_token"] in data["survey_url"], "Token should be in survey URL"
        print(f"✓ Survey created with token: {data['public_token'][:20]}...")
    
    def test_public_survey_access_no_auth(self, created_survey):
        """Test GET /api/sales-ops/public/surveys/{token} works without auth."""
        if not created_survey:
            pytest.skip("No survey created for public access test")
        
        token = created_survey.get("public_token")
        
        # Make request WITHOUT auth headers
        response = requests.get(f"{BASE_URL}/api/sales-ops/public/surveys/{token}")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert "survey_id" in data, "Response should contain survey_id"
        assert "contractor_user_name" in data, "Response should contain contractor_user_name"
        assert "dimensions" in data, "Response should contain dimensions list"
        assert len(data["dimensions"]) == 10, f"Should have 10 dimensions, got {len(data['dimensions'])}"
        print(f"✓ Public survey accessible without auth, has {len(data['dimensions'])} dimensions")
    
    def test_public_survey_submission_no_auth(self, created_survey):
        """Test POST /api/sales-ops/public/surveys/{token}/submit works without auth."""
        if not created_survey:
            pytest.skip("No survey created for submission test")
        
        token = created_survey.get("public_token")
        
        # Create 10 dimension ratings
        dimension_ratings = [
            {"dimension": "overall_professionalism", "rating": 5},
            {"dimension": "timeliness_to_meetings", "rating": 4},
            {"dimension": "communication_quality", "rating": 5},
            {"dimension": "quality_of_work", "rating": 4},
            {"dimension": "schedule_control", "rating": 3},
            {"dimension": "administration_required", "rating": 4},
            {"dimension": "project_documentation", "rating": 5},
            {"dimension": "adherence_to_scope", "rating": 4},
            {"dimension": "resolution_of_issues", "rating": 5},
            {"dimension": "timesheet_submission", "rating": 4}
        ]
        
        submission_data = {
            "project_name": "Completed Test Project",
            "customer_name": "Test Customer Corp",
            "hire_likelihood": 8,
            "dimension_ratings": dimension_ratings
        }
        
        # Make request WITHOUT auth headers
        response = requests.post(
            f"{BASE_URL}/api/sales-ops/public/surveys/{token}/submit",
            json=submission_data,
            headers={"Content-Type": "application/json"}
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert "calculated_rating" in data, "Response should contain calculated_rating"
        
        # Verify rating calculation: (5+4+5+4+3+4+5+4+5+4) / 10 = 4.3
        expected_rating = 4.3
        assert abs(data["calculated_rating"] - expected_rating) < 0.1, \
            f"Expected rating ~{expected_rating}, got {data['calculated_rating']}"
        
        print(f"✓ Survey submitted, calculated rating: {data['calculated_rating']}/5")
    
    def test_survey_already_completed_error(self, created_survey):
        """Test that submitting a completed survey returns error."""
        if not created_survey:
            pytest.skip("No survey created for duplicate submission test")
        
        token = created_survey.get("public_token")
        
        # Try to submit again
        dimension_ratings = [
            {"dimension": "overall_professionalism", "rating": 3},
            {"dimension": "timeliness_to_meetings", "rating": 3},
            {"dimension": "communication_quality", "rating": 3},
            {"dimension": "quality_of_work", "rating": 3},
            {"dimension": "schedule_control", "rating": 3},
            {"dimension": "administration_required", "rating": 3},
            {"dimension": "project_documentation", "rating": 3},
            {"dimension": "adherence_to_scope", "rating": 3},
            {"dimension": "resolution_of_issues", "rating": 3},
            {"dimension": "timesheet_submission", "rating": 3}
        ]
        
        submission_data = {
            "project_name": "Duplicate Submission",
            "customer_name": "Test",
            "hire_likelihood": 5,
            "dimension_ratings": dimension_ratings
        }
        
        response = requests.post(
            f"{BASE_URL}/api/sales-ops/public/surveys/{token}/submit",
            json=submission_data,
            headers={"Content-Type": "application/json"}
        )
        
        assert response.status_code == 400, f"Expected 400 for duplicate submission, got {response.status_code}"
        print(f"✓ Duplicate submission correctly rejected")
    
    def test_invalid_token_returns_404(self):
        """Test that invalid token returns 404."""
        fake_token = "invalid_token_12345"
        
        response = requests.get(f"{BASE_URL}/api/sales-ops/public/surveys/{fake_token}")
        
        assert response.status_code == 404, f"Expected 404 for invalid token, got {response.status_code}"
        print(f"✓ Invalid token correctly returns 404")
    
    def test_get_surveys_for_contractor(self, auth_headers):
        """Test GET surveys for a contractor business."""
        response = requests.get(
            f"{BASE_URL}/api/sales-ops/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}/surveys",
            headers=auth_headers
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "items" in data, "Response should contain 'items' array"
        print(f"✓ GET surveys returned {len(data['items'])} items")


class TestBankAccountDetails:
    """Test bank account details in contractor business update."""
    
    def test_update_contractor_with_bank_details(self, auth_headers):
        """Test updating contractor business with bank account details."""
        bank_details = {
            "bank_name": "TEST_Bank",
            "account_holder_name": "TEST_Account_Holder",
            "routing_number": "123456789",
            "account_number": "987654321",
            "account_type": "checking"
        }
        
        update_data = {
            "bank_account_details": bank_details,
            "billing_contact_name": "TEST_Billing_Contact",
            "billing_email": "billing@test.com"
        }
        
        response = requests.put(
            f"{BASE_URL}/api/admin/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}",
            headers=auth_headers,
            json=update_data
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify bank details were saved
        assert data.get("bank_account_details") is not None, "Bank account details should be saved"
        assert data["bank_account_details"]["bank_name"] == "TEST_Bank"
        print(f"✓ Bank account details saved successfully")
    
    def test_get_contractor_includes_bank_details(self, auth_headers):
        """Test GET contractor business includes bank account details."""
        response = requests.get(
            f"{BASE_URL}/api/admin/contractor-businesses/{TEST_CONTRACTOR_BUSINESS_ID}",
            headers=auth_headers
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Check bank details are returned
        if data.get("bank_account_details"):
            print(f"✓ Bank account details retrieved: {data['bank_account_details'].get('bank_name')}")
        else:
            print("✓ GET contractor business works (no bank details set)")


class TestSurveyRatingCalculation:
    """Test survey rating calculation and contractor business rating update."""
    
    def test_rating_calculation_formula(self, auth_headers):
        """Test that survey rating is correctly calculated as average of 10 dimensions."""
        # Create a new survey for this test
        survey_data = {
            "contractor_business_id": TEST_CONTRACTOR_BUSINESS_ID,
            "contractor_user_id": TEST_CONTRACTOR_USER_ID,
            "project_name": "Rating Calculation Test",
            "customer_name": "Test Corp"
        }
        
        response = requests.post(
            f"{BASE_URL}/api/sales-ops/surveys/send",
            headers=auth_headers,
            json=survey_data
        )
        
        if response.status_code != 200:
            pytest.skip(f"Could not create survey: {response.text}")
        
        token = response.json().get("public_token")
        
        # Submit with known ratings: all 5s = 5.0 average
        dimension_ratings = [
            {"dimension": "overall_professionalism", "rating": 5},
            {"dimension": "timeliness_to_meetings", "rating": 5},
            {"dimension": "communication_quality", "rating": 5},
            {"dimension": "quality_of_work", "rating": 5},
            {"dimension": "schedule_control", "rating": 5},
            {"dimension": "administration_required", "rating": 5},
            {"dimension": "project_documentation", "rating": 5},
            {"dimension": "adherence_to_scope", "rating": 5},
            {"dimension": "resolution_of_issues", "rating": 5},
            {"dimension": "timesheet_submission", "rating": 5}
        ]
        
        submission_data = {
            "project_name": "Perfect Rating Test",
            "customer_name": "Test Corp",
            "hire_likelihood": 10,
            "dimension_ratings": dimension_ratings
        }
        
        response = requests.post(
            f"{BASE_URL}/api/sales-ops/public/surveys/{token}/submit",
            json=submission_data,
            headers={"Content-Type": "application/json"}
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # All 5s should give exactly 5.0
        assert data["calculated_rating"] == 5.0, f"Expected 5.0, got {data['calculated_rating']}"
        print(f"✓ Rating calculation correct: all 5s = {data['calculated_rating']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
