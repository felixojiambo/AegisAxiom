"""
Test Commission Template Default User Selection Feature
Tests for:
1. Commission template creation with tier default users
2. Template update with default users
3. API retrieval of templates with default users
4. Dynamic variable storage ({deal_owner}, {agent_of_record})
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestCommissionTemplateDefaultUsers:
    """Test commission template default user selection feature"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup - authenticate before tests"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login to get auth token
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": "Admin123!"
        })
        assert login_response.status_code == 200, f"Login failed: {login_response.text}"
        
        token = login_response.json().get("access_token")
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        
        # Store created template IDs for cleanup
        self.created_templates = []
        yield
        
        # Cleanup: Delete test templates
        for template_id in self.created_templates:
            try:
                self.session.delete(f"{BASE_URL}/api/commission-management/templates/{template_id}")
            except Exception:
                pass

    def test_create_template_with_no_default_users(self):
        """Test creating template without default users (baseline)"""
        payload = {
            "name": "TEST_NoDefaultUsers_Template",
            "tier1_percentage": 10.0,
            "tier2_percentage": 5.0,
            "tier3_percentage": 15.0,
            "cost_type": "percentage",
            "cost_default": 50.0,
            "is_active": True
        }
        
        response = self.session.post(f"{BASE_URL}/api/commission-management/templates", json=payload)
        assert response.status_code == 200, f"Failed to create template: {response.text}"
        
        data = response.json()
        assert "template_id" in data
        self.created_templates.append(data["template_id"])
        print(f"✓ Created template without default users: {data['template_id']}")

    def test_create_template_with_deal_owner_dynamic_variable(self):
        """Test creating template with {deal_owner} dynamic variable"""
        payload = {
            "name": "TEST_DealOwner_Template",
            "tier1_percentage": 10.0,
            "tier2_percentage": 5.0,
            "tier3_percentage": 15.0,
            "cost_type": "percentage",
            "cost_default": 50.0,
            "is_active": True,
            "tier1_default_user": "{deal_owner}",
            "tier2_default_user": None,
            "tier3_default_user": None
        }
        
        response = self.session.post(f"{BASE_URL}/api/commission-management/templates", json=payload)
        assert response.status_code == 200, f"Failed to create template: {response.text}"
        
        data = response.json()
        assert "template_id" in data
        self.created_templates.append(data["template_id"])
        
        # Verify template was saved with the dynamic variable
        get_response = self.session.get(f"{BASE_URL}/api/commission-management/templates")
        assert get_response.status_code == 200
        
        templates = get_response.json().get("items", [])
        created_template = next((t for t in templates if t["template_id"] == data["template_id"]), None)
        assert created_template is not None, "Created template not found in list"
        assert created_template.get("tier1_default_user") == "{deal_owner}", \
            f"Expected tier1_default_user to be '{{deal_owner}}', got: {created_template.get('tier1_default_user')}"
        
        print(f"✓ Created template with {{deal_owner}}: {data['template_id']}")

    def test_create_template_with_agent_of_record_dynamic_variable(self):
        """Test creating template with {agent_of_record} dynamic variable"""
        payload = {
            "name": "TEST_AgentOfRecord_Template",
            "tier1_percentage": 8.0,
            "tier2_percentage": 6.0,
            "tier3_percentage": 12.0,
            "cost_type": "actual",
            "cost_default": 10000.0,
            "is_active": True,
            "tier1_default_user": None,
            "tier2_default_user": "{agent_of_record}",
            "tier3_default_user": None
        }
        
        response = self.session.post(f"{BASE_URL}/api/commission-management/templates", json=payload)
        assert response.status_code == 200, f"Failed to create template: {response.text}"
        
        data = response.json()
        assert "template_id" in data
        self.created_templates.append(data["template_id"])
        
        # Verify template was saved
        get_response = self.session.get(f"{BASE_URL}/api/commission-management/templates")
        assert get_response.status_code == 200
        
        templates = get_response.json().get("items", [])
        created_template = next((t for t in templates if t["template_id"] == data["template_id"]), None)
        assert created_template is not None
        assert created_template.get("tier2_default_user") == "{agent_of_record}", \
            f"Expected tier2_default_user to be '{{agent_of_record}}', got: {created_template.get('tier2_default_user')}"
        
        print(f"✓ Created template with {{agent_of_record}}: {data['template_id']}")

    def test_create_template_with_all_default_users(self):
        """Test creating template with all three tiers having default users"""
        payload = {
            "name": "TEST_AllTiers_Template",
            "tier1_percentage": 10.0,
            "tier2_percentage": 7.5,
            "tier3_percentage": 20.0,
            "cost_type": "percentage",
            "cost_default": 45.0,
            "is_active": True,
            "tier1_default_user": "{deal_owner}",
            "tier2_default_user": "{agent_of_record}",
            "tier3_default_user": "{deal_owner}"
        }
        
        response = self.session.post(f"{BASE_URL}/api/commission-management/templates", json=payload)
        assert response.status_code == 200, f"Failed to create template: {response.text}"
        
        data = response.json()
        assert "template_id" in data
        self.created_templates.append(data["template_id"])
        
        # Verify all three default users are stored
        get_response = self.session.get(f"{BASE_URL}/api/commission-management/templates")
        assert get_response.status_code == 200
        
        templates = get_response.json().get("items", [])
        created_template = next((t for t in templates if t["template_id"] == data["template_id"]), None)
        assert created_template is not None
        
        assert created_template.get("tier1_default_user") == "{deal_owner}"
        assert created_template.get("tier2_default_user") == "{agent_of_record}"
        assert created_template.get("tier3_default_user") == "{deal_owner}"
        
        print(f"✓ Created template with all three tiers having default users: {data['template_id']}")

    def test_update_template_with_default_users(self):
        """Test updating template to add/change default users"""
        # First create a template without default users
        payload = {
            "name": "TEST_UpdateDefaultUsers_Template",
            "tier1_percentage": 10.0,
            "tier2_percentage": 5.0,
            "tier3_percentage": 15.0,
            "cost_type": "percentage",
            "cost_default": 50.0,
            "is_active": True
        }
        
        create_response = self.session.post(f"{BASE_URL}/api/commission-management/templates", json=payload)
        assert create_response.status_code == 200
        
        template_id = create_response.json()["template_id"]
        self.created_templates.append(template_id)
        
        # Update the template with default users
        update_payload = {
            "name": "TEST_UpdateDefaultUsers_Template_Updated",
            "tier1_percentage": 12.0,
            "tier2_percentage": 6.0,
            "tier3_percentage": 18.0,
            "cost_type": "percentage",
            "cost_default": 55.0,
            "is_active": True,
            "tier1_default_user": "{deal_owner}",
            "tier2_default_user": "{agent_of_record}",
            "tier3_default_user": None
        }
        
        update_response = self.session.put(
            f"{BASE_URL}/api/commission-management/templates/{template_id}", 
            json=update_payload
        )
        assert update_response.status_code == 200, f"Failed to update template: {update_response.text}"
        
        # Verify the update
        get_response = self.session.get(f"{BASE_URL}/api/commission-management/templates")
        assert get_response.status_code == 200
        
        templates = get_response.json().get("items", [])
        updated_template = next((t for t in templates if t["template_id"] == template_id), None)
        assert updated_template is not None
        
        assert updated_template.get("tier1_default_user") == "{deal_owner}"
        assert updated_template.get("tier2_default_user") == "{agent_of_record}"
        assert updated_template.get("tier1_percentage") == 12.0
        
        print(f"✓ Updated template with default users: {template_id}")

    def test_template_with_specific_user_id(self):
        """Test creating template with a specific user_id as default user"""
        # First get a user ID from the users list
        users_response = self.session.get(f"{BASE_URL}/api/admin/users")
        assert users_response.status_code == 200, f"Failed to get users: {users_response.text}"
        
        users = users_response.json()
        if not users or len(users) == 0:
            pytest.skip("No users available to test specific user_id")
        
        specific_user_id = users[0].get("user_id")
        specific_user_name = users[0].get("name")
        
        payload = {
            "name": "TEST_SpecificUserId_Template",
            "tier1_percentage": 10.0,
            "tier2_percentage": 5.0,
            "tier3_percentage": 15.0,
            "cost_type": "percentage",
            "cost_default": 50.0,
            "is_active": True,
            "tier1_default_user": specific_user_id,
            "tier2_default_user": None,
            "tier3_default_user": None
        }
        
        response = self.session.post(f"{BASE_URL}/api/commission-management/templates", json=payload)
        assert response.status_code == 200, f"Failed to create template: {response.text}"
        
        data = response.json()
        assert "template_id" in data
        self.created_templates.append(data["template_id"])
        
        # Verify specific user_id was stored
        get_response = self.session.get(f"{BASE_URL}/api/commission-management/templates")
        assert get_response.status_code == 200
        
        templates = get_response.json().get("items", [])
        created_template = next((t for t in templates if t["template_id"] == data["template_id"]), None)
        assert created_template is not None
        assert created_template.get("tier1_default_user") == specific_user_id, \
            f"Expected tier1_default_user to be '{specific_user_id}', got: {created_template.get('tier1_default_user')}"
        
        print(f"✓ Created template with specific user_id ({specific_user_name}): {data['template_id']}")

    def test_get_templates_returns_default_users(self):
        """Test that GET /templates returns default user fields"""
        # Create a template with default users
        payload = {
            "name": "TEST_GetTemplateDefaultUsers",
            "tier1_percentage": 10.0,
            "tier2_percentage": 5.0,
            "tier3_percentage": 15.0,
            "cost_type": "percentage",
            "cost_default": 50.0,
            "is_active": True,
            "tier1_default_user": "{deal_owner}",
            "tier2_default_user": "{agent_of_record}",
            "tier3_default_user": None
        }
        
        create_response = self.session.post(f"{BASE_URL}/api/commission-management/templates", json=payload)
        assert create_response.status_code == 200
        
        template_id = create_response.json()["template_id"]
        self.created_templates.append(template_id)
        
        # Get templates and verify default user fields exist
        get_response = self.session.get(f"{BASE_URL}/api/commission-management/templates")
        assert get_response.status_code == 200
        
        templates = get_response.json().get("items", [])
        created_template = next((t for t in templates if t["template_id"] == template_id), None)
        
        assert created_template is not None
        # Verify the default user fields are present in the response
        assert "tier1_default_user" in created_template or created_template.get("tier1_default_user") == "{deal_owner}"
        assert "tier2_default_user" in created_template or created_template.get("tier2_default_user") == "{agent_of_record}"
        
        print(f"✓ GET templates returns default user fields correctly")

    def test_delete_template_with_default_users(self):
        """Test deleting a template with default users"""
        payload = {
            "name": "TEST_DeleteTemplate_DefaultUsers",
            "tier1_percentage": 10.0,
            "tier2_percentage": 5.0,
            "tier3_percentage": 15.0,
            "cost_type": "percentage",
            "cost_default": 50.0,
            "is_active": True,
            "tier1_default_user": "{deal_owner}",
            "tier2_default_user": None,
            "tier3_default_user": None
        }
        
        create_response = self.session.post(f"{BASE_URL}/api/commission-management/templates", json=payload)
        assert create_response.status_code == 200
        
        template_id = create_response.json()["template_id"]
        
        # Delete the template
        delete_response = self.session.delete(f"{BASE_URL}/api/commission-management/templates/{template_id}")
        assert delete_response.status_code == 200, f"Failed to delete template: {delete_response.text}"
        
        # Verify it's soft deleted (is_active = false, so won't appear in list)
        get_response = self.session.get(f"{BASE_URL}/api/commission-management/templates")
        assert get_response.status_code == 200
        
        templates = get_response.json().get("items", [])
        deleted_template = next((t for t in templates if t["template_id"] == template_id), None)
        assert deleted_template is None, "Deleted template should not appear in active templates list"
        
        print(f"✓ Successfully deleted template with default users: {template_id}")


class TestCommissionTemplateAPIStructure:
    """Test the API structure for commission template default users"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup - authenticate before tests"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login to get auth token
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": "Admin123!"
        })
        assert login_response.status_code == 200
        
        token = login_response.json().get("access_token")
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        yield

    def test_api_endpoints_accessible(self):
        """Test that commission template endpoints are accessible"""
        # Test GET templates endpoint
        response = self.session.get(f"{BASE_URL}/api/commission-management/templates")
        assert response.status_code == 200, f"GET templates failed: {response.text}"
        
        data = response.json()
        assert "items" in data, "Response should contain 'items' field"
        print(f"✓ Templates endpoint accessible, found {len(data['items'])} templates")

    def test_users_api_accessible(self):
        """Test that users API is accessible for dropdown population"""
        response = self.session.get(f"{BASE_URL}/api/admin/users")
        assert response.status_code == 200, f"GET users failed: {response.text}"
        
        users = response.json()
        assert isinstance(users, list), "Users response should be a list"
        print(f"✓ Users endpoint accessible, found {len(users)} users")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
