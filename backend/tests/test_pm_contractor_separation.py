"""
Test PM vs Contractor Separation - Dragon Ball users as internal PMs, contractors from businesses
Tests:
1. GET /api/resources?role=project_manager returns ONLY Dragon Ball PMs (5 users)
2. GET /api/resources/contractors returns ONLY contractor role users (10 contractors)
3. Dragon Ball users have role=project_manager and contractor_business_id=null
4. Create project with project_manager_id from Dragon Ball team works
5. Create task with assigned_to from contractor list works
6. Task assignment resolves contractor name correctly
"""
import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestPMContractorSeparation:
    """Test separation of internal PMs from contractors"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test fixtures - login and get auth token"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login as admin
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": "Admin@123!"
        })
        
        if login_response.status_code != 200:
            pytest.skip("Authentication failed - skipping tests")
        
        data = login_response.json()
        self.token = data.get("token")
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})
        
        # Store test data for cleanup
        self.test_project_id = None
        self.test_task_id = None
        
        yield
        
        # Cleanup
        if self.test_task_id:
            self.session.delete(f"{BASE_URL}/api/pm/tasks/{self.test_task_id}")
        if self.test_project_id:
            self.session.delete(f"{BASE_URL}/api/pm/projects/{self.test_project_id}")
    
    # ==================== Resources API Tests ====================
    
    def test_resources_role_project_manager_returns_only_dragon_ball_pms(self):
        """Test GET /api/resources?role=project_manager returns ONLY Dragon Ball PMs"""
        response = self.session.get(f"{BASE_URL}/api/resources?role=project_manager")
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        items = data.get("items", [])
        
        # Should have exactly 5 Dragon Ball PMs
        dragon_ball_emails = [
            "goku@simplimeta.io",
            "vegeta@simplimeta.io",
            "piccolo@simplimeta.io",
            "gohan@simplimeta.io",
            "trunks@simplimeta.io"
        ]
        
        # Verify count
        assert len(items) == 5, f"Expected 5 Dragon Ball PMs, got {len(items)}: {[u.get('email') for u in items]}"
        
        # Verify all are Dragon Ball characters
        for user in items:
            assert user.get("email") in dragon_ball_emails, f"Unexpected user in PM list: {user.get('email')}"
            assert user.get("role") == "project_manager", f"User {user.get('email')} has wrong role: {user.get('role')}"
            assert user.get("is_contractor") == False, f"PM {user.get('email')} should not be marked as contractor"
            assert user.get("contractor_business_id") is None, f"PM {user.get('email')} should not have contractor_business_id"
            print(f"✓ PM: {user.get('name')} ({user.get('email')})")
        
        print(f"\n✓ Total PMs returned: {len(items)}")
    
    def test_resources_contractors_returns_only_contractors(self):
        """Test GET /api/resources/contractors returns ONLY contractor role users"""
        response = self.session.get(f"{BASE_URL}/api/resources/contractors")
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        items = data.get("items", [])
        
        # Should have 10 contractors
        assert len(items) == 10, f"Expected 10 contractors, got {len(items)}"
        
        # Verify all are contractors with business IDs
        for user in items:
            assert user.get("contractor_business_id") is not None, f"Contractor {user.get('email')} missing contractor_business_id"
            assert user.get("contractor_business_name") is not None, f"Contractor {user.get('email')} missing contractor_business_name"
            print(f"✓ Contractor: {user.get('name')} ({user.get('contractor_business_name')})")
        
        # Verify Dragon Ball users are NOT in contractor list
        dragon_ball_emails = ["goku@simplimeta.io", "vegeta@simplimeta.io", "piccolo@simplimeta.io", "gohan@simplimeta.io", "trunks@simplimeta.io"]
        contractor_emails = [u.get("email") for u in items]
        
        for db_email in dragon_ball_emails:
            assert db_email not in contractor_emails, f"Dragon Ball PM {db_email} should NOT be in contractor list"
        
        print(f"\n✓ Total contractors returned: {len(items)}")
    
    def test_dragon_ball_users_have_correct_role_and_no_business(self):
        """Test Dragon Ball users have role=project_manager and contractor_business_id=null"""
        # Get all users
        response = self.session.get(f"{BASE_URL}/api/admin/users")
        assert response.status_code == 200
        
        data = response.json()
        users = data if isinstance(data, list) else data.get("items", [])
        
        dragon_ball_emails = [
            "goku@simplimeta.io",
            "vegeta@simplimeta.io",
            "piccolo@simplimeta.io",
            "gohan@simplimeta.io",
            "trunks@simplimeta.io"
        ]
        
        for email in dragon_ball_emails:
            user = next((u for u in users if u.get("email") == email), None)
            assert user is not None, f"Dragon Ball user {email} not found"
            assert user.get("role") == "project_manager", f"{email} should have role=project_manager, got {user.get('role')}"
            assert user.get("contractor_business_id") is None, f"{email} should have contractor_business_id=null"
            print(f"✓ {user.get('name')}: role={user.get('role')}, contractor_business_id={user.get('contractor_business_id')}")
    
    def test_contractors_have_business_associations(self):
        """Test contractors have contractor_business_id and business names"""
        response = self.session.get(f"{BASE_URL}/api/resources/contractors")
        assert response.status_code == 200
        
        data = response.json()
        items = data.get("items", [])
        
        # Expected businesses (actual names from database)
        expected_businesses = ["SHIELD Contractors LLC", "Stark Industries Tech Services", "Avengers Technical Solutions"]
        found_businesses = set()
        
        for contractor in items:
            business_name = contractor.get("contractor_business_name")
            assert business_name in expected_businesses, f"Unexpected business: {business_name}"
            found_businesses.add(business_name)
            print(f"✓ {contractor.get('name')} - {business_name}")
        
        # Verify all 3 businesses are represented
        assert len(found_businesses) == 3, f"Expected 3 businesses, found {len(found_businesses)}: {found_businesses}"
        print(f"\n✓ All 3 contractor businesses represented: {found_businesses}")
    
    # ==================== Project Creation Tests ====================
    
    def test_create_project_with_dragon_ball_pm(self):
        """Test creating project with Dragon Ball PM works correctly"""
        # Get Piccolo's user_id
        pm_response = self.session.get(f"{BASE_URL}/api/resources?role=project_manager")
        assert pm_response.status_code == 200
        pms = pm_response.json().get("items", [])
        
        piccolo = next((u for u in pms if u.get("email") == "piccolo@simplimeta.io"), None)
        assert piccolo is not None, "Piccolo not found in PM list"
        
        # Create project with Piccolo as PM
        project_data = {
            "name": f"TEST_Piccolo_Project_{uuid.uuid4().hex[:8]}",
            "description": "Test project with Piccolo as PM",
            "project_type": "boh",
            "sale_price": 50000,
            "margin_goal_percent": 35,
            "purchased_hours": 100,
            "project_manager_id": piccolo["user_id"],
            "is_test_data": True
        }
        
        response = self.session.post(f"{BASE_URL}/api/pm/projects", json=project_data)
        assert response.status_code == 200, f"Failed to create project: {response.text}"
        
        project = response.json()
        self.test_project_id = project.get("project_id")
        
        # Verify PM is set correctly
        assert project.get("project_manager_id") == piccolo["user_id"]
        assert "Piccolo" in project.get("project_manager_name", ""), f"Expected 'Piccolo' in PM name, got {project.get('project_manager_name')}"
        
        print(f"✓ Created project with PM: {project.get('project_manager_name')}")
    
    # ==================== Task Assignment Tests ====================
    
    def test_create_task_with_contractor_assignment(self):
        """Test creating task with contractor from contractor list"""
        # First create a project
        project_data = {
            "name": f"TEST_ContractorTask_Project_{uuid.uuid4().hex[:8]}",
            "project_type": "boh",
            "sale_price": 30000,
            "margin_goal_percent": 35,
            "is_test_data": True
        }
        
        project_response = self.session.post(f"{BASE_URL}/api/pm/projects", json=project_data)
        assert project_response.status_code == 200
        project = project_response.json()
        self.test_project_id = project["project_id"]
        
        # Get contractors
        contractors_response = self.session.get(f"{BASE_URL}/api/resources/contractors")
        assert contractors_response.status_code == 200
        contractors = contractors_response.json().get("items", [])
        
        # Find Tony Stark
        tony = next((c for c in contractors if "Tony" in c.get("name", "")), None)
        if not tony:
            # Use first contractor if Tony not found
            tony = contractors[0] if contractors else None
        
        assert tony is not None, "No contractors found"
        
        # Create task assigned to contractor
        task_data = {
            "name": "TEST_Contractor Task",
            "description": "Task assigned to contractor",
            "assigned_to": tony["user_id"],
            "estimated_hours": 8.0,
            "priority": "medium"
        }
        
        task_response = self.session.post(
            f"{BASE_URL}/api/pm/projects/{self.test_project_id}/tasks",
            json=task_data
        )
        assert task_response.status_code == 200, f"Failed to create task: {task_response.text}"
        
        task = task_response.json()
        self.test_task_id = task.get("task_id")
        
        # Verify assignment
        assert task.get("assigned_to") == tony["user_id"]
        assert task.get("assigned_to_name") is not None, "assigned_to_name should be populated"
        
        print(f"✓ Created task assigned to: {task.get('assigned_to_name')}")
    
    def test_task_assignment_resolves_contractor_name(self):
        """Test that task assignment correctly resolves contractor name"""
        # Create project
        project_data = {
            "name": f"TEST_NameResolve_Project_{uuid.uuid4().hex[:8]}",
            "project_type": "boh",
            "sale_price": 20000,
            "margin_goal_percent": 35,
            "is_test_data": True
        }
        
        project_response = self.session.post(f"{BASE_URL}/api/pm/projects", json=project_data)
        assert project_response.status_code == 200
        project = project_response.json()
        self.test_project_id = project["project_id"]
        
        # Get contractors
        contractors_response = self.session.get(f"{BASE_URL}/api/resources/contractors")
        contractors = contractors_response.json().get("items", [])
        
        # Pick a contractor
        contractor = contractors[0] if contractors else None
        assert contractor is not None, "No contractors found"
        
        # Create task
        task_data = {
            "name": "TEST_Name Resolution Task",
            "assigned_to": contractor["user_id"],
            "estimated_hours": 4.0
        }
        
        task_response = self.session.post(
            f"{BASE_URL}/api/pm/projects/{self.test_project_id}/tasks",
            json=task_data
        )
        assert task_response.status_code == 200
        task = task_response.json()
        self.test_task_id = task.get("task_id")
        
        # Get project detail to verify name resolution
        detail_response = self.session.get(f"{BASE_URL}/api/pm/projects/{self.test_project_id}")
        assert detail_response.status_code == 200
        
        detail = detail_response.json()
        tasks = detail.get("tasks", [])
        our_task = next((t for t in tasks if t.get("task_id") == self.test_task_id), None)
        
        assert our_task is not None
        assert our_task.get("assigned_to_name") == contractor.get("name"), \
            f"Expected assigned_to_name='{contractor.get('name')}', got '{our_task.get('assigned_to_name')}'"
        
        print(f"✓ Task assigned_to_name correctly resolved to: {our_task.get('assigned_to_name')}")
    
    # ==================== Dashboard Verification Tests ====================
    
    def test_dashboard_shows_pm_name_correctly(self):
        """Test dashboard shows PM name for projects"""
        # Get PMs
        pm_response = self.session.get(f"{BASE_URL}/api/resources?role=project_manager")
        pms = pm_response.json().get("items", [])
        goku = next((u for u in pms if u.get("email") == "goku@simplimeta.io"), None)
        
        if not goku:
            pytest.skip("Goku not found")
        
        # Create project with Goku as PM
        project_data = {
            "name": f"TEST_Dashboard_PM_{uuid.uuid4().hex[:8]}",
            "project_type": "boh",
            "sale_price": 40000,
            "margin_goal_percent": 35,
            "project_manager_id": goku["user_id"],
            "is_test_data": True
        }
        
        project_response = self.session.post(f"{BASE_URL}/api/pm/projects", json=project_data)
        assert project_response.status_code == 200
        project = project_response.json()
        self.test_project_id = project["project_id"]
        
        # Get dashboard
        dashboard_response = self.session.get(f"{BASE_URL}/api/pm/projects/dashboard?is_test_data=true")
        assert dashboard_response.status_code == 200
        
        dashboard = dashboard_response.json()
        projects = dashboard.get("projects", [])
        
        # Find our project
        our_project = next((p for p in projects if p.get("project_id") == self.test_project_id), None)
        assert our_project is not None, "Test project not found in dashboard"
        
        # Verify PM name
        assert "Goku" in our_project.get("project_manager_name", ""), \
            f"Expected 'Goku' in PM name, got {our_project.get('project_manager_name')}"
        
        print(f"✓ Dashboard shows PM: {our_project.get('project_manager_name')}")


class TestContractorBusinessDetails:
    """Test contractor business details are correctly populated"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test fixtures"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": "Admin@123!"
        })
        
        if login_response.status_code != 200:
            pytest.skip("Authentication failed")
        
        data = login_response.json()
        self.token = data.get("token")
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})
    
    def test_contractors_have_business_names_in_response(self):
        """Test contractors include business name in response"""
        response = self.session.get(f"{BASE_URL}/api/resources/contractors")
        assert response.status_code == 200
        
        data = response.json()
        items = data.get("items", [])
        
        for contractor in items:
            assert "contractor_business_name" in contractor, f"Missing contractor_business_name for {contractor.get('name')}"
            assert contractor.get("contractor_business_name") is not None, f"Null contractor_business_name for {contractor.get('name')}"
            print(f"✓ {contractor.get('name')} ({contractor.get('contractor_business_name')})")
    
    def test_pm_dropdown_excludes_contractors(self):
        """Test PM dropdown (role=project_manager) excludes all contractors"""
        # Get PMs
        pm_response = self.session.get(f"{BASE_URL}/api/resources?role=project_manager")
        assert pm_response.status_code == 200
        pms = pm_response.json().get("items", [])
        
        # Get contractors
        contractor_response = self.session.get(f"{BASE_URL}/api/resources/contractors")
        assert contractor_response.status_code == 200
        contractors = contractor_response.json().get("items", [])
        
        pm_user_ids = {u.get("user_id") for u in pms}
        contractor_user_ids = {u.get("user_id") for u in contractors}
        
        # Verify no overlap
        overlap = pm_user_ids.intersection(contractor_user_ids)
        assert len(overlap) == 0, f"Found users in both PM and contractor lists: {overlap}"
        
        print(f"✓ No overlap between {len(pms)} PMs and {len(contractors)} contractors")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
