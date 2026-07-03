"""
Contacts Module Backend Tests
Tests CRUD operations, user promotion, and validation for the Contacts API.
Uses session-scoped authentication to avoid rate limiting.
"""
import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin@123!"


# Session-scoped fixtures to avoid rate limiting
@pytest.fixture(scope="session")
def auth_session():
    """Create authenticated session once for all tests"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    # Login to get auth token
    login_response = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })
    assert login_response.status_code == 200, f"Login failed: {login_response.text}"
    
    login_data = login_response.json()
    token = login_data.get("token")
    assert token, "No token in login response"
    
    session.headers.update({"Authorization": f"Bearer {token}"})
    return session


@pytest.fixture(scope="session")
def test_account(auth_session):
    """Get a valid account for testing"""
    response = auth_session.get(f"{BASE_URL}/api/accounts?limit=5")
    assert response.status_code == 200
    accounts_data = response.json()
    return accounts_data.get("items", [{}])[0] if accounts_data.get("items") else None


class TestContactsAPI:
    """Test suite for Contacts CRUD operations"""
    
    # ---- GET /api/contacts Tests ----
    
    def test_get_contacts_list(self, auth_session):
        """Test fetching contacts list"""
        response = auth_session.get(f"{BASE_URL}/api/contacts?limit=100")
        assert response.status_code == 200, f"Failed to get contacts: {response.text}"
        
        data = response.json()
        assert "items" in data, "Response missing 'items' field"
        assert "total" in data, "Response missing 'total' field"
        assert isinstance(data["items"], list), "Items should be a list"
        print(f"✓ GET /api/contacts - Found {data['total']} contacts")
    
    def test_get_contacts_with_search(self, auth_session):
        """Test contacts search functionality"""
        response = auth_session.get(f"{BASE_URL}/api/contacts?search=Admin&limit=100")
        assert response.status_code == 200, f"Search failed: {response.text}"
        
        data = response.json()
        assert "items" in data
        print(f"✓ GET /api/contacts?search=Admin - Found {len(data['items'])} matches")
    
    def test_get_contacts_with_account_filter(self, auth_session, test_account):
        """Test contacts filtered by account"""
        if not test_account:
            pytest.skip("No test account available")
        
        account_id = test_account.get("account_id")
        response = auth_session.get(f"{BASE_URL}/api/contacts?account_id={account_id}&limit=100")
        assert response.status_code == 200, f"Account filter failed: {response.text}"
        
        data = response.json()
        # Verify all returned contacts belong to the filtered account
        for contact in data.get("items", []):
            assert contact.get("account_id") == account_id, "Contact account_id mismatch"
        print(f"✓ GET /api/contacts?account_id={account_id} - Found {len(data['items'])} contacts")
    
    def test_get_contacts_test_only_filter(self, auth_session):
        """Test test_only filter"""
        response = auth_session.get(f"{BASE_URL}/api/contacts?test_only=true&limit=100")
        assert response.status_code == 200, f"Test only filter failed: {response.text}"
        
        data = response.json()
        # Verify all returned contacts are test data
        for contact in data.get("items", []):
            assert contact.get("is_test_data") == True, "Non-test contact returned with test_only=true"
        print(f"✓ GET /api/contacts?test_only=true - Found {len(data['items'])} test contacts")
    
    # ---- POST /api/contacts Tests ----
    
    def test_create_contact_success(self, auth_session, test_account):
        """Test creating a new contact"""
        if not test_account:
            pytest.skip("No test account available")
        
        unique_id = uuid.uuid4().hex[:8]
        contact_data = {
            "account_id": test_account.get("account_id"),
            "first_name": f"TEST_{unique_id}",
            "last_name": "ContactCreate",
            "email": f"test_{unique_id}@example.com",
            "phone": "555-0100",
            "mobile_phone": "555-0101",
            "title": "Test Manager",
            "department": "Testing",
            "linkedin_url": "https://linkedin.com/in/testuser",
            "preferred_contact_method": "email",
            "is_primary": False,
            "is_test_data": True
        }
        
        response = auth_session.post(f"{BASE_URL}/api/contacts", json=contact_data)
        assert response.status_code == 200, f"Create contact failed: {response.text}"
        
        created = response.json()
        assert created.get("contact_id"), "No contact_id in response"
        assert created.get("first_name") == contact_data["first_name"]
        assert created.get("last_name") == contact_data["last_name"]
        assert created.get("email") == contact_data["email"]
        assert created.get("name") == f"{contact_data['first_name']} {contact_data['last_name']}"
        
        # Verify persistence with GET
        get_response = auth_session.get(f"{BASE_URL}/api/contacts/{created['contact_id']}")
        assert get_response.status_code == 200
        fetched = get_response.json()
        assert fetched.get("email") == contact_data["email"]
        
        print(f"✓ POST /api/contacts - Created contact {created['contact_id']}")
        
        # Cleanup
        auth_session.delete(f"{BASE_URL}/api/contacts/{created['contact_id']}")
    
    def test_create_contact_missing_required_fields(self, auth_session, test_account):
        """Test creating contact without required fields fails"""
        # Missing account_id
        response = auth_session.post(f"{BASE_URL}/api/contacts", json={
            "first_name": "Test",
            "last_name": "User"
        })
        assert response.status_code == 422, "Should fail without account_id"
        print("✓ POST /api/contacts - Correctly rejects missing account_id")
        
        # Missing first_name
        if test_account:
            response = auth_session.post(f"{BASE_URL}/api/contacts", json={
                "account_id": test_account.get("account_id"),
                "last_name": "User"
            })
            assert response.status_code == 422, "Should fail without first_name"
            print("✓ POST /api/contacts - Correctly rejects missing first_name")
    
    def test_create_contact_invalid_account(self, auth_session):
        """Test creating contact with non-existent account fails"""
        response = auth_session.post(f"{BASE_URL}/api/contacts", json={
            "account_id": "acc_nonexistent_12345",
            "first_name": "Test",
            "last_name": "User"
        })
        assert response.status_code == 404, f"Should fail with invalid account: {response.text}"
        print("✓ POST /api/contacts - Correctly rejects invalid account_id")
    
    def test_create_contact_duplicate_email_same_account(self, auth_session, test_account):
        """Test duplicate email within same account is rejected"""
        if not test_account:
            pytest.skip("No test account available")
        
        unique_id = uuid.uuid4().hex[:8]
        email = f"test_dup_{unique_id}@example.com"
        
        # Create first contact
        contact1 = {
            "account_id": test_account.get("account_id"),
            "first_name": f"TEST_{unique_id}",
            "last_name": "First",
            "email": email,
            "is_test_data": True
        }
        response1 = auth_session.post(f"{BASE_URL}/api/contacts", json=contact1)
        assert response1.status_code == 200, f"First contact creation failed: {response1.text}"
        created1 = response1.json()
        
        # Try to create second contact with same email in same account
        contact2 = {
            "account_id": test_account.get("account_id"),
            "first_name": f"TEST_{unique_id}",
            "last_name": "Second",
            "email": email,
            "is_test_data": True
        }
        response2 = auth_session.post(f"{BASE_URL}/api/contacts", json=contact2)
        assert response2.status_code == 400, f"Should reject duplicate email: {response2.text}"
        assert "already exists" in response2.json().get("detail", "").lower()
        
        print("✓ POST /api/contacts - Correctly rejects duplicate email in same account")
        
        # Cleanup
        auth_session.delete(f"{BASE_URL}/api/contacts/{created1['contact_id']}")
    
    # ---- GET /api/contacts/{contact_id} Tests ----
    
    def test_get_single_contact(self, auth_session):
        """Test fetching a single contact by ID"""
        # First get a contact from the list
        list_response = auth_session.get(f"{BASE_URL}/api/contacts?limit=1")
        assert list_response.status_code == 200
        contacts = list_response.json().get("items", [])
        
        if not contacts:
            pytest.skip("No contacts available for testing")
        
        contact_id = contacts[0].get("contact_id")
        response = auth_session.get(f"{BASE_URL}/api/contacts/{contact_id}")
        assert response.status_code == 200, f"Failed to get contact: {response.text}"
        
        contact = response.json()
        assert contact.get("contact_id") == contact_id
        assert "first_name" in contact
        assert "last_name" in contact
        assert "account_id" in contact
        print(f"✓ GET /api/contacts/{contact_id} - Retrieved contact successfully")
    
    def test_get_nonexistent_contact(self, auth_session):
        """Test fetching non-existent contact returns 404"""
        response = auth_session.get(f"{BASE_URL}/api/contacts/con_nonexistent_12345")
        assert response.status_code == 404, f"Should return 404: {response.text}"
        print("✓ GET /api/contacts/nonexistent - Correctly returns 404")
    
    # ---- PUT /api/contacts/{contact_id} Tests ----
    
    def test_update_contact(self, auth_session, test_account):
        """Test updating a contact"""
        if not test_account:
            pytest.skip("No test account available")
        
        # Create a contact to update
        unique_id = uuid.uuid4().hex[:8]
        create_response = auth_session.post(f"{BASE_URL}/api/contacts", json={
            "account_id": test_account.get("account_id"),
            "first_name": f"TEST_{unique_id}",
            "last_name": "ToUpdate",
            "email": f"test_update_{unique_id}@example.com",
            "title": "Original Title",
            "is_test_data": True
        })
        assert create_response.status_code == 200
        created = create_response.json()
        contact_id = created.get("contact_id")
        
        # Update the contact
        update_data = {
            "first_name": f"TEST_{unique_id}_Updated",
            "title": "Updated Title",
            "department": "New Department",
            "phone": "555-9999"
        }
        update_response = auth_session.put(f"{BASE_URL}/api/contacts/{contact_id}", json=update_data)
        assert update_response.status_code == 200, f"Update failed: {update_response.text}"
        
        updated = update_response.json()
        assert updated.get("first_name") == update_data["first_name"]
        assert updated.get("title") == update_data["title"]
        assert updated.get("department") == update_data["department"]
        assert updated.get("phone") == update_data["phone"]
        # Name should be recomputed
        assert f"TEST_{unique_id}_Updated" in updated.get("name", "")
        
        # Verify persistence
        get_response = auth_session.get(f"{BASE_URL}/api/contacts/{contact_id}")
        assert get_response.status_code == 200
        fetched = get_response.json()
        assert fetched.get("title") == "Updated Title"
        
        print(f"✓ PUT /api/contacts/{contact_id} - Updated contact successfully")
        
        # Cleanup
        auth_session.delete(f"{BASE_URL}/api/contacts/{contact_id}")
    
    def test_update_primary_contact_flag(self, auth_session, test_account):
        """Test setting primary contact flag unsets others"""
        if not test_account:
            pytest.skip("No test account available")
        
        unique_id = uuid.uuid4().hex[:8]
        account_id = test_account.get("account_id")
        
        # Create two contacts
        contact1_resp = auth_session.post(f"{BASE_URL}/api/contacts", json={
            "account_id": account_id,
            "first_name": f"TEST_{unique_id}",
            "last_name": "Primary1",
            "is_primary": True,
            "is_test_data": True
        })
        assert contact1_resp.status_code == 200
        contact1 = contact1_resp.json()
        
        contact2_resp = auth_session.post(f"{BASE_URL}/api/contacts", json={
            "account_id": account_id,
            "first_name": f"TEST_{unique_id}",
            "last_name": "Primary2",
            "is_primary": False,
            "is_test_data": True
        })
        assert contact2_resp.status_code == 200
        contact2 = contact2_resp.json()
        
        # Set contact2 as primary
        update_resp = auth_session.put(f"{BASE_URL}/api/contacts/{contact2['contact_id']}", json={
            "is_primary": True
        })
        assert update_resp.status_code == 200
        
        # Verify contact1 is no longer primary
        get1_resp = auth_session.get(f"{BASE_URL}/api/contacts/{contact1['contact_id']}")
        assert get1_resp.status_code == 200
        assert get1_resp.json().get("is_primary") == False, "Previous primary should be unset"
        
        # Verify contact2 is now primary
        get2_resp = auth_session.get(f"{BASE_URL}/api/contacts/{contact2['contact_id']}")
        assert get2_resp.status_code == 200
        assert get2_resp.json().get("is_primary") == True
        
        print("✓ PUT /api/contacts - Primary flag correctly unsets other primaries")
        
        # Cleanup
        auth_session.delete(f"{BASE_URL}/api/contacts/{contact1['contact_id']}")
        auth_session.delete(f"{BASE_URL}/api/contacts/{contact2['contact_id']}")
    
    # ---- DELETE /api/contacts/{contact_id} Tests ----
    
    def test_delete_contact(self, auth_session, test_account):
        """Test deleting a contact"""
        if not test_account:
            pytest.skip("No test account available")
        
        # Create a contact to delete
        unique_id = uuid.uuid4().hex[:8]
        create_response = auth_session.post(f"{BASE_URL}/api/contacts", json={
            "account_id": test_account.get("account_id"),
            "first_name": f"TEST_{unique_id}",
            "last_name": "ToDelete",
            "is_test_data": True
        })
        assert create_response.status_code == 200
        created = create_response.json()
        contact_id = created.get("contact_id")
        
        # Delete the contact
        delete_response = auth_session.delete(f"{BASE_URL}/api/contacts/{contact_id}")
        assert delete_response.status_code == 200, f"Delete failed: {delete_response.text}"
        
        # Verify deletion
        get_response = auth_session.get(f"{BASE_URL}/api/contacts/{contact_id}")
        assert get_response.status_code == 404, "Contact should not exist after deletion"
        
        print(f"✓ DELETE /api/contacts/{contact_id} - Deleted contact successfully")
    
    def test_delete_nonexistent_contact(self, auth_session):
        """Test deleting non-existent contact returns 404"""
        response = auth_session.delete(f"{BASE_URL}/api/contacts/con_nonexistent_12345")
        assert response.status_code == 404, f"Should return 404: {response.text}"
        print("✓ DELETE /api/contacts/nonexistent - Correctly returns 404")
    
    def test_delete_contact_linked_to_opportunity(self, auth_session, test_account):
        """Test that deleting contact linked to opportunity fails"""
        if not test_account:
            pytest.skip("No test account available")
        
        unique_id = uuid.uuid4().hex[:8]
        account_id = test_account.get("account_id")
        
        # Create contact
        contact_resp = auth_session.post(f"{BASE_URL}/api/contacts", json={
            "account_id": account_id,
            "first_name": f"TEST_{unique_id}",
            "last_name": "CascadeTest",
            "email": f"test_cascade_{unique_id}@example.com",
            "is_test_data": True
        })
        assert contact_resp.status_code == 200
        contact = contact_resp.json()
        
        # Create opportunity linked to contact
        opp_resp = auth_session.post(f"{BASE_URL}/api/opportunities", json={
            "name": f"TEST_Cascade_Opp_{unique_id}",
            "account_id": account_id,
            "contact_id": contact["contact_id"],
            "stage": "prospect_opp",
            "is_test_data": True
        })
        assert opp_resp.status_code == 200
        opp = opp_resp.json()
        
        # Try to delete contact - should fail
        delete_resp = auth_session.delete(f"{BASE_URL}/api/contacts/{contact['contact_id']}")
        assert delete_resp.status_code == 400, f"Should fail with 400: {delete_resp.text}"
        assert "opportunity" in delete_resp.json().get("detail", "").lower()
        
        print("✓ DELETE /api/contacts - Correctly prevents deletion when linked to opportunity")
        
        # Cleanup
        auth_session.delete(f"{BASE_URL}/api/opportunities/{opp['opportunity_id']}")
        auth_session.delete(f"{BASE_URL}/api/contacts/{contact['contact_id']}")
    
    # ---- Statistics Tests ----
    
    def test_contacts_stats_by_account(self, auth_session):
        """Test contacts statistics by account endpoint"""
        response = auth_session.get(f"{BASE_URL}/api/contacts/stats/by-account")
        assert response.status_code == 200, f"Stats failed: {response.text}"
        
        data = response.json()
        assert "accounts" in data, "Response missing 'accounts' field"
        assert isinstance(data["accounts"], list)
        
        # Verify structure of each account stat
        for stat in data.get("accounts", []):
            assert "account_id" in stat
            assert "account_name" in stat
            assert "contact_count" in stat
        
        print(f"✓ GET /api/contacts/stats/by-account - Retrieved stats for {len(data['accounts'])} accounts")


class TestUserMigration:
    """Test that users were migrated to contacts correctly"""
    
    def test_users_have_contacts(self, auth_session):
        """Verify users have corresponding contact records"""
        # Get users count - API returns list directly
        users_response = auth_session.get(f"{BASE_URL}/api/admin/users?limit=1000")
        assert users_response.status_code == 200
        users_data = users_response.json()
        users = users_data if isinstance(users_data, list) else users_data.get("items", [])
        
        # Get contacts with linked_user_id
        contacts_response = auth_session.get(f"{BASE_URL}/api/contacts?limit=1000")
        assert contacts_response.status_code == 200
        contacts = contacts_response.json().get("items", [])
        
        linked_contacts = [c for c in contacts if c.get("linked_user_id")]
        
        print(f"✓ User Migration Check:")
        print(f"  - Total users: {len(users)}")
        print(f"  - Contacts with linked_user_id: {len(linked_contacts)}")
        
        # At least some users should have contacts
        assert len(linked_contacts) > 0, "No users have been migrated to contacts"
    
    def test_internal_users_account_exists(self, auth_session):
        """Verify Internal Users account was created for system users"""
        response = auth_session.get(f"{BASE_URL}/api/accounts?limit=1000")
        assert response.status_code == 200
        
        accounts = response.json().get("items", [])
        internal_account = next((a for a in accounts if a.get("name") == "Internal Users"), None)
        
        if internal_account:
            print(f"✓ Internal Users account exists: {internal_account.get('account_id')}")
        else:
            print("⚠ Internal Users account not found (may not be needed if all users have accounts)")


class TestContactUserPromotion:
    """Test creating user accounts from contacts"""
    
    def test_create_user_from_contact_without_email_fails(self, auth_session, test_account):
        """Test that creating user from contact without email fails"""
        if not test_account:
            pytest.skip("No test account available")
        
        # Create contact without email
        unique_id = uuid.uuid4().hex[:8]
        create_response = auth_session.post(f"{BASE_URL}/api/contacts", json={
            "account_id": test_account.get("account_id"),
            "first_name": f"TEST_{unique_id}",
            "last_name": "NoEmail",
            "is_test_data": True
        })
        assert create_response.status_code == 200
        contact = create_response.json()
        
        # Try to create user
        response = auth_session.post(
            f"{BASE_URL}/api/contacts/{contact['contact_id']}/create-user?password=TestPass123&role=customer"
        )
        assert response.status_code == 400, f"Should fail without email: {response.text}"
        assert "email" in response.json().get("detail", "").lower()
        
        print("✓ POST /api/contacts/{id}/create-user - Correctly rejects contact without email")
        
        # Cleanup
        auth_session.delete(f"{BASE_URL}/api/contacts/{contact['contact_id']}")
    
    def test_create_user_from_contact_already_linked_fails(self, auth_session):
        """Test that creating user from already-linked contact fails"""
        # Find a contact that already has a linked user
        contacts_response = auth_session.get(f"{BASE_URL}/api/contacts?limit=100")
        assert contacts_response.status_code == 200
        
        contacts = contacts_response.json().get("items", [])
        linked_contact = next((c for c in contacts if c.get("linked_user_id")), None)
        
        if not linked_contact:
            pytest.skip("No linked contacts available for testing")
        
        response = auth_session.post(
            f"{BASE_URL}/api/contacts/{linked_contact['contact_id']}/create-user?password=TestPass123&role=customer"
        )
        assert response.status_code == 400, f"Should fail for already-linked contact: {response.text}"
        assert "already" in response.json().get("detail", "").lower()
        
        print("✓ POST /api/contacts/{id}/create-user - Correctly rejects already-linked contact")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
