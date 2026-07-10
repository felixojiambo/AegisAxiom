"""
Test Data Filter Tests
Tests for the test_only filter functionality in Opportunities and Contacts APIs.
Verifies that admin users can filter test data correctly.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin@123!"


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token for admin user."""
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    data = response.json()
    # API returns 'token' not 'access_token'
    assert "token" in data, "No token in response"
    return data["token"]


@pytest.fixture(scope="module")
def api_client(auth_token):
    """Create authenticated session."""
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json"
    })
    return session


class TestOpportunitiesTestDataFilter:
    """Tests for Opportunities test_only filter."""
    
    def test_get_opportunities_without_test_filter(self, api_client):
        """Get all opportunities without test_only filter."""
        response = api_client.get(f"{BASE_URL}/api/opportunities?limit=500")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        print(f"Total opportunities (no filter): {data['total']}")
        return data
    
    def test_get_opportunities_with_test_only_true(self, api_client):
        """Get only test opportunities with test_only=true."""
        response = api_client.get(f"{BASE_URL}/api/opportunities?test_only=true&limit=500")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        
        # Verify all returned items have is_test_data=True
        for item in data["items"]:
            assert item.get("is_test_data") == True, f"Found non-test opportunity: {item.get('name')}"
        
        print(f"Test opportunities count: {data['total']}")
        return data
    
    def test_opportunities_test_filter_count_matches(self, api_client):
        """Verify test_only filter returns correct count."""
        # Get all opportunities
        all_response = api_client.get(f"{BASE_URL}/api/opportunities?limit=500")
        all_data = all_response.json()
        
        # Get test-only opportunities
        test_response = api_client.get(f"{BASE_URL}/api/opportunities?test_only=true&limit=500")
        test_data = test_response.json()
        
        # Count test data in all opportunities
        test_count_in_all = sum(1 for item in all_data["items"] if item.get("is_test_data") == True)
        
        print(f"All opportunities: {all_data['total']}")
        print(f"Test opportunities (filter): {test_data['total']}")
        print(f"Test opportunities (counted): {test_count_in_all}")
        
        # The test_only filter count should match the count of test data in all
        assert test_data["total"] == test_count_in_all, \
            f"Test filter count ({test_data['total']}) doesn't match actual test data count ({test_count_in_all})"
    
    def test_opportunities_stats_endpoint(self, api_client):
        """Test opportunities stats by stage endpoint."""
        response = api_client.get(f"{BASE_URL}/api/opportunities/stats/by-stage")
        assert response.status_code == 200
        data = response.json()
        
        # Should have stage keys
        expected_stages = [
            "prospect_opp", "prospect_lead", "proposal_development",
            "scoping_complete", "proposal_sent", "negotiation_review",
            "verbal_agreement", "closed_won", "closed_lost"
        ]
        
        for stage in expected_stages:
            assert stage in data, f"Missing stage: {stage}"
            assert "count" in data[stage]
            assert "total_amount" in data[stage]
        
        print(f"Opportunities stats: {data}")


class TestContactsTestDataFilter:
    """Tests for Contacts test_only filter."""
    
    def test_get_contacts_without_test_filter(self, api_client):
        """Get all contacts without test_only filter."""
        response = api_client.get(f"{BASE_URL}/api/contacts?limit=1000")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        print(f"Total contacts (no filter): {data['total']}")
        return data
    
    def test_get_contacts_with_test_only_true(self, api_client):
        """Get only test contacts with test_only=true."""
        response = api_client.get(f"{BASE_URL}/api/contacts?test_only=true&limit=1000")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        
        # Verify all returned items have is_test_data=True
        for item in data["items"]:
            assert item.get("is_test_data") == True, f"Found non-test contact: {item.get('name')}"
        
        print(f"Test contacts count: {data['total']}")
        return data
    
    def test_contacts_test_filter_count_matches(self, api_client):
        """Verify test_only filter returns correct count."""
        # Get all contacts
        all_response = api_client.get(f"{BASE_URL}/api/contacts?limit=1000")
        all_data = all_response.json()
        
        # Get test-only contacts
        test_response = api_client.get(f"{BASE_URL}/api/contacts?test_only=true&limit=1000")
        test_data = test_response.json()
        
        # Count test data in all contacts
        test_count_in_all = sum(1 for item in all_data["items"] if item.get("is_test_data") == True)
        
        print(f"All contacts: {all_data['total']}")
        print(f"Test contacts (filter): {test_data['total']}")
        print(f"Test contacts (counted): {test_count_in_all}")
        
        # The test_only filter count should match the count of test data in all
        assert test_data["total"] == test_count_in_all, \
            f"Test filter count ({test_data['total']}) doesn't match actual test data count ({test_count_in_all})"
    
    def test_contacts_include_fictional_characters(self, api_client):
        """Verify test contacts include fictional characters like Bruce Wayne."""
        response = api_client.get(f"{BASE_URL}/api/contacts?test_only=true&limit=1000")
        assert response.status_code == 200
        data = response.json()
        
        # Check for some expected fictional characters
        contact_names = [c.get("name", "") for c in data["items"]]
        
        # Check for at least some fictional characters
        fictional_chars = ["Bruce Wayne", "Clark Kent", "Tony Stark", "Diana Prince"]
        found_chars = [char for char in fictional_chars if char in contact_names]
        
        print(f"Found fictional characters: {found_chars}")
        assert len(found_chars) > 0, "No fictional characters found in test contacts"


class TestWayneEnterprisesOpportunity:
    """Test creating Wayne Enterprises opportunity with Bruce Wayne as contact."""
    
    def test_get_wayne_enterprises_account(self, api_client):
        """Find Wayne Enterprises account."""
        response = api_client.get(f"{BASE_URL}/api/accounts?search=Wayne&limit=100")
        assert response.status_code == 200
        data = response.json()
        
        wayne_accounts = [a for a in data["items"] if "Wayne" in a.get("name", "")]
        print(f"Wayne accounts found: {[a.get('name') for a in wayne_accounts]}")
        
        assert len(wayne_accounts) > 0, "Wayne Enterprises account not found"
        return wayne_accounts[0]
    
    def test_get_bruce_wayne_contact(self, api_client):
        """Find Bruce Wayne contact."""
        response = api_client.get(f"{BASE_URL}/api/contacts?search=Bruce%20Wayne&limit=100")
        assert response.status_code == 200
        data = response.json()
        
        bruce_contacts = [c for c in data["items"] if "Bruce Wayne" in c.get("name", "")]
        print(f"Bruce Wayne contacts found: {[c.get('name') for c in bruce_contacts]}")
        
        assert len(bruce_contacts) > 0, "Bruce Wayne contact not found"
        return bruce_contacts[0]
    
    def test_create_wayne_opportunity_with_bruce_wayne(self, api_client):
        """Create an opportunity for Wayne Enterprises with Bruce Wayne as contact."""
        # Get Wayne Enterprises account
        acc_response = api_client.get(f"{BASE_URL}/api/accounts?search=Wayne&limit=100")
        acc_data = acc_response.json()
        wayne_accounts = [a for a in acc_data["items"] if "Wayne" in a.get("name", "")]
        
        if not wayne_accounts:
            pytest.skip("Wayne Enterprises account not found")
        
        wayne_account = wayne_accounts[0]
        account_id = wayne_account.get("account_id")
        
        # Get Bruce Wayne contact
        contact_response = api_client.get(f"{BASE_URL}/api/contacts?account_id={account_id}&limit=100")
        contact_data = contact_response.json()
        bruce_contacts = [c for c in contact_data["items"] if "Bruce" in c.get("name", "")]
        
        if not bruce_contacts:
            pytest.skip("Bruce Wayne contact not found for Wayne Enterprises")
        
        bruce_contact = bruce_contacts[0]
        contact_id = bruce_contact.get("contact_id")
        
        # Create opportunity
        opp_data = {
            "name": "TEST_Wayne Enterprises Security Upgrade",
            "account_id": account_id,
            "contact_id": contact_id,
            "description": "Security system upgrade for Wayne Tower",
            "amount": 500000,
            "stage": "prospect_opp",
            "priority": "high",
            "is_test_data": True
        }
        
        response = api_client.post(f"{BASE_URL}/api/opportunities", json=opp_data)
        assert response.status_code == 200, f"Failed to create opportunity: {response.text}"
        
        created_opp = response.json()
        print(f"Created opportunity: {created_opp.get('name')}")
        print(f"Account: {created_opp.get('account_name')}")
        print(f"Contact: {created_opp.get('contact_name')}")
        
        assert created_opp.get("account_name") == wayne_account.get("name")
        assert created_opp.get("contact_name") == bruce_contact.get("name")
        
        # Cleanup - delete the test opportunity
        opp_id = created_opp.get("opportunity_id")
        if opp_id:
            api_client.delete(f"{BASE_URL}/api/opportunities/{opp_id}")
            print(f"Cleaned up test opportunity: {opp_id}")


class TestUserRoleVerification:
    """Verify user role for test data default behavior."""
    
    def test_admin_user_role(self, api_client, auth_token):
        """Verify the test user has admin role."""
        response = api_client.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 200
        user_data = response.json()
        
        print(f"User: {user_data.get('email')}")
        print(f"Role: {user_data.get('role')}")
        
        assert user_data.get("role") == "admin", \
            f"Expected admin role, got: {user_data.get('role')}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
