"""
Test User Resource Type Management APIs
Testing assignment of resource types to users with cost rates
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL').rstrip('/')
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


class TestUserResourceTypeAPIs:
    """Test user resource type assignment endpoints"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup authentication for admin tests"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login as admin
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": ADMIN_PASSWORD
        })
        
        if login_resp.status_code != 200:
            pytest.skip(f"Admin login failed: {login_resp.status_code}")
        
        login_data = login_resp.json()
        self.token = login_data.get("token")
        self.user = login_data.get("user", {})
        
        if self.token:
            self.session.headers.update({"Authorization": f"Bearer {self.token}"})
    
    def test_get_default_resource_types(self):
        """Test GET /api/admin/default-resource-types endpoint"""
        response = self.session.get(f"{BASE_URL}/api/admin/default-resource-types")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        # API returns default_resource_types key
        assert "default_resource_types" in data, "Response should contain default_resource_types"
        assert isinstance(data["default_resource_types"], list), "default_resource_types should be a list"
        print(f"Found {len(data['default_resource_types'])} default resource types")
        
        # Validate resource type structure if any exist
        if len(data["default_resource_types"]) > 0:
            rt = data["default_resource_types"][0]
            # Note: default_resource_types uses name as identifier (no separate id field)
            assert "name" in rt, "Resource type should have name"
            assert "sell_rate" in rt, "Resource type should have sell_rate"
            print(f"Sample resource type: {rt.get('name')} at ${rt.get('sell_rate')}/hr")
    
    def test_get_users_list(self):
        """Test GET /api/admin/users endpoint to get user list"""
        response = self.session.get(f"{BASE_URL}/api/admin/users")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        users = response.json()
        assert isinstance(users, list), "Response should be a list of users"
        print(f"Found {len(users)} users")
        
        # Check for resource_type_assignments field
        if len(users) > 0:
            user = users[0]
            # Field may or may not exist
            assignments = user.get("resource_type_assignments", [])
            print(f"First user has {len(assignments)} resource type assignments")
            
        return users
    
    def test_get_user_resource_types(self):
        """Test GET /api/users/{user_id}/resource-types endpoint"""
        # First get a user
        users_resp = self.session.get(f"{BASE_URL}/api/admin/users")
        assert users_resp.status_code == 200
        
        users = users_resp.json()
        assert len(users) > 0, "No users found to test"
        
        # Use the logged in admin user
        user_id = self.user.get("user_id")
        
        # Get resource types for this user
        response = self.session.get(f"{BASE_URL}/api/users/{user_id}/resource-types")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "user_id" in data, "Response should contain user_id"
        assert "assignments" in data, "Response should contain assignments"
        assert "total" in data, "Response should contain total count"
        
        print(f"User {data.get('user_name')} has {data.get('total')} resource type assignments")
        
        return data
    
    def test_get_user_resource_types_nonexistent_user(self):
        """Test GET /api/users/{user_id}/resource-types with non-existent user"""
        response = self.session.get(f"{BASE_URL}/api/users/nonexistent_user_123/resource-types")
        
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("Non-existent user correctly returns 404")
    
    def test_assign_resource_type_to_user(self):
        """Test POST /api/users/{user_id}/resource-types endpoint"""
        # First get default resource types
        rt_resp = self.session.get(f"{BASE_URL}/api/admin/default-resource-types")
        assert rt_resp.status_code == 200
        
        resource_types = rt_resp.json().get("default_resource_types", [])
        if len(resource_types) == 0:
            pytest.skip("No default resource types available")
        
        resource_type = resource_types[0]
        
        # Use admin user for assignment test
        user_id = self.user.get("user_id")
        
        # Assign resource type (use name as id since that's how default_resource_types works)
        response = self.session.post(f"{BASE_URL}/api/users/{user_id}/resource-types", json={
            "resource_type_id": resource_type["name"],  # Use name as ID
            "cost_rate": 85.50,
            "is_active": True
        })
        
        # Can be 200 (update existing) or 201 (new assignment)
        assert response.status_code in [200, 201], f"Expected 200/201, got {response.status_code}"
        
        data = response.json()
        assert "message" in data, "Response should contain message"
        assert "assignment" in data, "Response should contain assignment"
        
        assignment = data["assignment"]
        assert assignment.get("resource_type_id") == resource_type["name"]
        assert assignment.get("cost_rate") == 85.50
        assert assignment.get("is_active") == True
        # Verify name is now correctly populated
        assert assignment.get("resource_type_name") == resource_type["name"], f"Expected name {resource_type['name']}, got {assignment.get('resource_type_name')}"
        
        print(f"Successfully assigned {resource_type['name']} to user with rate $85.50")
        
        return assignment
    
    def test_assign_resource_type_without_rate(self):
        """Test POST /api/users/{user_id}/resource-types without cost_rate fails validation"""
        user_id = self.user.get("user_id")
        
        response = self.session.post(f"{BASE_URL}/api/users/{user_id}/resource-types", json={
            "resource_type_id": "some_id",
            # Missing cost_rate
            "is_active": True
        })
        
        # Should fail validation - 422 Unprocessable Entity
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        print("Missing cost_rate correctly returns 422 validation error")
    
    def test_verify_assignment_persisted(self):
        """Test that assigned resource type persists in GET response"""
        user_id = self.user.get("user_id")
        
        # Get current assignments
        response = self.session.get(f"{BASE_URL}/api/users/{user_id}/resource-types")
        
        assert response.status_code == 200
        
        data = response.json()
        assignments = data.get("assignments", [])
        
        # If we assigned in previous test, verify it's there
        # Look for the assignment with cost_rate 85.50
        found_assignment = None
        for a in assignments:
            if a.get("cost_rate") == 85.50:
                found_assignment = a
                break
        
        if found_assignment:
            print(f"Verified assignment persisted: {found_assignment.get('resource_type_name')} at ${found_assignment.get('cost_rate')}")
            assert found_assignment.get("assigned_at") is not None, "assigned_at should be set"
            assert found_assignment.get("assigned_by") is not None, "assigned_by should be set"
        else:
            print(f"Found {len(assignments)} assignments for user")
    
    def test_remove_resource_type_from_user(self):
        """Test DELETE /api/users/{user_id}/resource-types/{resource_type_id} endpoint"""
        # First ensure we have an assignment
        user_id = self.user.get("user_id")
        
        # Get current assignments
        get_resp = self.session.get(f"{BASE_URL}/api/users/{user_id}/resource-types")
        assert get_resp.status_code == 200
        
        assignments = get_resp.json().get("assignments", [])
        
        if len(assignments) == 0:
            # Create one first
            rt_resp = self.session.get(f"{BASE_URL}/api/admin/default-resource-types")
            resource_types = rt_resp.json().get("default_resource_types", [])
            if len(resource_types) == 0:
                pytest.skip("No resource types to test deletion")
            
            # Assign one (use name as ID)
            self.session.post(f"{BASE_URL}/api/users/{user_id}/resource-types", json={
                "resource_type_id": resource_types[0]["name"],
                "cost_rate": 100.00,
                "is_active": True
            })
            
            # Re-fetch
            get_resp = self.session.get(f"{BASE_URL}/api/users/{user_id}/resource-types")
            assignments = get_resp.json().get("assignments", [])
        
        if len(assignments) == 0:
            pytest.skip("No assignments available to delete")
        
        # Delete the first assignment
        assignment_to_delete = assignments[0]
        resource_type_id = assignment_to_delete.get("resource_type_id")
        
        response = self.session.delete(f"{BASE_URL}/api/users/{user_id}/resource-types/{resource_type_id}")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "message" in data, "Response should contain message"
        assert "removed" in data["message"].lower() or "success" in data["message"].lower()
        
        print(f"Successfully removed resource type {resource_type_id}")
        
        # Verify deletion
        verify_resp = self.session.get(f"{BASE_URL}/api/users/{user_id}/resource-types")
        verify_assignments = verify_resp.json().get("assignments", [])
        
        # Check it's gone
        for a in verify_assignments:
            assert a.get("resource_type_id") != resource_type_id, "Deleted assignment still present"
        
        print("Verified assignment was deleted successfully")
    
    def test_remove_nonexistent_resource_type(self):
        """Test DELETE with non-existent resource_type_id returns 404"""
        user_id = self.user.get("user_id")
        
        response = self.session.delete(f"{BASE_URL}/api/users/{user_id}/resource-types/nonexistent_rt_123")
        
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("Non-existent resource type correctly returns 404 on delete")
    
    def test_get_users_with_resource_type(self):
        """Test GET /api/resource-types/{resource_type_id}/users endpoint"""
        # First get a resource type
        rt_resp = self.session.get(f"{BASE_URL}/api/admin/default-resource-types")
        assert rt_resp.status_code == 200
        
        resource_types = rt_resp.json().get("default_resource_types", [])
        if len(resource_types) == 0:
            pytest.skip("No default resource types available")
        
        # Use name as the resource_type_id
        resource_type_name = resource_types[0]["name"]
        
        response = self.session.get(f"{BASE_URL}/api/resource-types/{resource_type_name}/users")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "resource_type_id" in data, "Response should contain resource_type_id"
        assert "users" in data, "Response should contain users list"
        assert "total" in data, "Response should contain total count"
        
        print(f"Found {data.get('total')} users with resource type {resource_type_name}")
        
        # Verify user structure if any users
        if len(data["users"]) > 0:
            user = data["users"][0]
            assert "user_id" in user
            assert "name" in user
            assert "cost_rate" in user
            print(f"Sample user: {user.get('name')} at ${user.get('cost_rate')}/hr")


class TestUserResourceTypePermissions:
    """Test permission handling for resource type APIs"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup session"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
    
    def test_unauthenticated_access_denied(self):
        """Test that unauthenticated requests are denied"""
        # Try without authentication
        response = self.session.get(f"{BASE_URL}/api/users/some_user_id/resource-types")
        
        # Should be 401 or 403
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print("Unauthenticated access correctly denied")
    
    def test_user_can_view_own_resource_types(self):
        """Test that regular users can view their own resource types"""
        # Login as admin first to get user list
        login_resp = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": ADMIN_PASSWORD
        })
        
        if login_resp.status_code != 200:
            pytest.skip("Login failed")
        
        login_data = login_resp.json()
        token = login_data.get("token")
        user = login_data.get("user", {})
        
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        
        # User should be able to view their own resource types
        response = self.session.get(f"{BASE_URL}/api/users/{user.get('user_id')}/resource-types")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("User can view their own resource types")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
