"""
Test PM Project Management - Dragon Ball PM Users, Milestone Assignment, Resource Assignment, Hours Tracking
Tests:
1. Dragon Ball PM users exist and can be assigned to projects
2. POST /api/pm/projects with project_manager_id creates project with PM name resolved
3. GET /api/pm/projects/dashboard returns projects with project_manager_name populated
4. POST /api/pm/projects/{project_id}/tasks accepts milestone_id, assigned_to, estimated_hours, actual_hours
5. GET /api/pm/projects/{project_id} returns tasks with milestone_name populated
6. PUT /api/pm/tasks/{task_id} updates milestone_id, assigned_to, estimated_hours, actual_hours
"""
import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestDragonBallPMFeatures:
    """Test Dragon Ball PM users and enhanced task features"""
    
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
        self.test_milestone_id = None
        self.test_task_id = None
        
        yield
        
        # Cleanup
        if self.test_task_id:
            self.session.delete(f"{BASE_URL}/api/pm/tasks/{self.test_task_id}")
        if self.test_milestone_id:
            self.session.delete(f"{BASE_URL}/api/pm/milestones/{self.test_milestone_id}")
        if self.test_project_id:
            self.session.delete(f"{BASE_URL}/api/pm/projects/{self.test_project_id}")
    
    # ==================== Dragon Ball PM Users Tests ====================
    
    def test_dragon_ball_users_exist(self):
        """Test that Dragon Ball PM users exist in the system"""
        response = self.session.get(f"{BASE_URL}/api/admin/users")
        assert response.status_code == 200
        
        data = response.json()
        # API returns list directly, not {"items": [...]}
        users = data if isinstance(data, list) else data.get("items", [])
        
        # Check for Dragon Ball characters
        dragon_ball_emails = [
            "goku@simplimeta.io",
            "vegeta@simplimeta.io",
            "piccolo@simplimeta.io",
            "gohan@simplimeta.io",
            "trunks@simplimeta.io"
        ]
        
        found_users = [u for u in users if u.get("email") in dragon_ball_emails]
        assert len(found_users) >= 1, f"Expected at least 1 Dragon Ball PM user, found {len(found_users)}"
        
        # Verify user structure
        for user in found_users:
            assert "user_id" in user
            assert "name" in user
            assert "email" in user
            print(f"Found Dragon Ball PM: {user['name']} ({user['email']})")
    
    def test_get_goku_user_details(self):
        """Test that Goku user exists and has correct details"""
        response = self.session.get(f"{BASE_URL}/api/admin/users")
        assert response.status_code == 200
        
        data = response.json()
        users = data if isinstance(data, list) else data.get("items", [])
        
        goku = next((u for u in users if u.get("email") == "goku@simplimeta.io"), None)
        assert goku is not None, "Goku user not found"
        assert "Goku" in goku.get("name", ""), f"Expected 'Goku' in name, got {goku.get('name')}"
        
        # Store Goku's user_id for later tests
        self.goku_user_id = goku["user_id"]
        print(f"Goku user_id: {self.goku_user_id}")
    
    # ==================== Project Creation with PM Tests ====================
    
    def test_create_project_with_pm_resolves_name(self):
        """Test POST /api/pm/projects with project_manager_id resolves PM name"""
        # First get Goku's user_id
        users_response = self.session.get(f"{BASE_URL}/api/admin/users")
        users_data = users_response.json()
        users = users_data if isinstance(users_data, list) else users_data.get("items", [])
        goku = next((u for u in users if u.get("email") == "goku@simplimeta.io"), None)
        
        if not goku:
            pytest.skip("Goku user not found - skipping test")
        
        goku_user_id = goku["user_id"]
        
        # Create project with PM
        project_data = {
            "name": f"TEST_DragonBall_Project_{uuid.uuid4().hex[:8]}",
            "description": "Test project with Dragon Ball PM",
            "project_type": "boh",
            "sale_price": 50000,
            "margin_goal_percent": 35,
            "purchased_hours": 100,
            "project_manager_id": goku_user_id,
            "is_test_data": True
        }
        
        response = self.session.post(f"{BASE_URL}/api/pm/projects", json=project_data)
        assert response.status_code == 200, f"Failed to create project: {response.text}"
        
        data = response.json()
        self.test_project_id = data.get("project_id")
        
        # Verify PM name is resolved
        assert data.get("project_manager_id") == goku_user_id
        assert data.get("project_manager_name") is not None, "project_manager_name should be populated"
        assert "Goku" in data.get("project_manager_name", ""), f"Expected 'Goku' in PM name, got {data.get('project_manager_name')}"
        
        print(f"Created project with PM: {data.get('project_manager_name')}")
    
    def test_dashboard_returns_pm_name(self):
        """Test GET /api/pm/projects/dashboard returns projects with project_manager_name"""
        response = self.session.get(f"{BASE_URL}/api/pm/projects/dashboard?is_test_data=true")
        assert response.status_code == 200
        
        data = response.json()
        projects = data.get("projects", [])
        
        # Find projects with PM assigned
        projects_with_pm = [p for p in projects if p.get("project_manager_id")]
        
        if projects_with_pm:
            for project in projects_with_pm:
                # Verify project_manager_name is populated when PM is assigned
                if project.get("project_manager_id"):
                    assert project.get("project_manager_name") is not None or project.get("project_manager_name") == "", \
                        f"Project {project.get('name')} has PM ID but no PM name"
                    print(f"Project '{project.get('name')}' has PM: {project.get('project_manager_name')}")
    
    # ==================== Milestone Tests ====================
    
    def test_create_milestone_for_project(self):
        """Test creating a milestone for a project"""
        # First create a project
        project_data = {
            "name": f"TEST_Milestone_Project_{uuid.uuid4().hex[:8]}",
            "project_type": "boh",
            "sale_price": 10000,
            "margin_goal_percent": 35,
            "is_test_data": True
        }
        
        project_response = self.session.post(f"{BASE_URL}/api/pm/projects", json=project_data)
        assert project_response.status_code == 200
        project = project_response.json()
        self.test_project_id = project["project_id"]
        
        # Create milestone
        milestone_data = {
            "name": "TEST_Phase 1: Setup",
            "description": "Initial setup phase",
            "due_date": "2026-02-15",
            "order": 1
        }
        
        milestone_response = self.session.post(
            f"{BASE_URL}/api/pm/projects/{self.test_project_id}/milestones",
            json=milestone_data
        )
        assert milestone_response.status_code == 200, f"Failed to create milestone: {milestone_response.text}"
        
        milestone = milestone_response.json()
        self.test_milestone_id = milestone.get("milestone_id")
        
        assert milestone.get("name") == "TEST_Phase 1: Setup"
        assert milestone.get("project_id") == self.test_project_id
        print(f"Created milestone: {milestone.get('name')} (ID: {self.test_milestone_id})")
    
    # ==================== Task Creation with All Fields Tests ====================
    
    def test_create_task_with_milestone_and_hours(self):
        """Test POST /api/pm/projects/{project_id}/tasks with milestone_id, assigned_to, estimated_hours, actual_hours"""
        # Create project
        project_data = {
            "name": f"TEST_Task_Project_{uuid.uuid4().hex[:8]}",
            "project_type": "boh",
            "sale_price": 20000,
            "margin_goal_percent": 35,
            "is_test_data": True
        }
        
        project_response = self.session.post(f"{BASE_URL}/api/pm/projects", json=project_data)
        assert project_response.status_code == 200
        project = project_response.json()
        self.test_project_id = project["project_id"]
        
        # Create milestone
        milestone_data = {
            "name": "TEST_Development Phase",
            "order": 1
        }
        milestone_response = self.session.post(
            f"{BASE_URL}/api/pm/projects/{self.test_project_id}/milestones",
            json=milestone_data
        )
        assert milestone_response.status_code == 200
        milestone = milestone_response.json()
        self.test_milestone_id = milestone["milestone_id"]
        
        # Get a user for assignment
        users_response = self.session.get(f"{BASE_URL}/api/admin/users")
        users_data = users_response.json()
        users = users_data if isinstance(users_data, list) else users_data.get("items", [])
        goku = next((u for u in users if u.get("email") == "goku@simplimeta.io"), None)
        assigned_user_id = goku["user_id"] if goku else None
        
        # Create task with all fields
        task_data = {
            "name": "TEST_Implement Feature X",
            "description": "Implement the main feature",
            "milestone_id": self.test_milestone_id,
            "assigned_to": assigned_user_id,
            "estimated_hours": 16.0,
            "actual_hours": 4.5,
            "priority": "high",
            "due_date": "2026-02-10"
        }
        
        task_response = self.session.post(
            f"{BASE_URL}/api/pm/projects/{self.test_project_id}/tasks",
            json=task_data
        )
        assert task_response.status_code == 200, f"Failed to create task: {task_response.text}"
        
        task = task_response.json()
        self.test_task_id = task.get("task_id")
        
        # Verify all fields are saved correctly
        assert task.get("name") == "TEST_Implement Feature X"
        assert task.get("milestone_id") == self.test_milestone_id
        assert task.get("assigned_to") == assigned_user_id
        assert task.get("estimated_hours") == 16.0
        assert task.get("actual_hours") == 4.5, f"Expected actual_hours=4.5, got {task.get('actual_hours')}"
        
        if assigned_user_id:
            assert task.get("assigned_to_name") is not None, "assigned_to_name should be populated"
        
        print(f"Created task with milestone_id={self.test_milestone_id}, actual_hours={task.get('actual_hours')}")
    
    def test_get_project_returns_tasks_with_milestone_name(self):
        """Test GET /api/pm/projects/{project_id} returns tasks with milestone_name populated"""
        # Create project
        project_data = {
            "name": f"TEST_MilestoneName_Project_{uuid.uuid4().hex[:8]}",
            "project_type": "boh",
            "sale_price": 15000,
            "margin_goal_percent": 35,
            "is_test_data": True
        }
        
        project_response = self.session.post(f"{BASE_URL}/api/pm/projects", json=project_data)
        assert project_response.status_code == 200
        project = project_response.json()
        self.test_project_id = project["project_id"]
        
        # Create milestone
        milestone_data = {
            "name": "TEST_Backend Development",
            "order": 1
        }
        milestone_response = self.session.post(
            f"{BASE_URL}/api/pm/projects/{self.test_project_id}/milestones",
            json=milestone_data
        )
        assert milestone_response.status_code == 200
        milestone = milestone_response.json()
        self.test_milestone_id = milestone["milestone_id"]
        
        # Create task with milestone
        task_data = {
            "name": "TEST_API Development",
            "milestone_id": self.test_milestone_id,
            "estimated_hours": 8.0,
            "actual_hours": 2.0
        }
        
        task_response = self.session.post(
            f"{BASE_URL}/api/pm/projects/{self.test_project_id}/tasks",
            json=task_data
        )
        assert task_response.status_code == 200
        task = task_response.json()
        self.test_task_id = task["task_id"]
        
        # Get project detail and verify milestone_name is populated
        detail_response = self.session.get(f"{BASE_URL}/api/pm/projects/{self.test_project_id}")
        assert detail_response.status_code == 200
        
        detail = detail_response.json()
        tasks = detail.get("tasks", [])
        
        # Find our task
        our_task = next((t for t in tasks if t.get("task_id") == self.test_task_id), None)
        assert our_task is not None, "Task not found in project detail"
        
        # Verify milestone_name is populated
        assert our_task.get("milestone_name") == "TEST_Backend Development", \
            f"Expected milestone_name='TEST_Backend Development', got {our_task.get('milestone_name')}"
        
        print(f"Task has milestone_name: {our_task.get('milestone_name')}")
    
    # ==================== Task Update Tests ====================
    
    def test_update_task_milestone_and_hours(self):
        """Test PUT /api/pm/tasks/{task_id} updates milestone_id, assigned_to, estimated_hours, actual_hours"""
        # Create project
        project_data = {
            "name": f"TEST_UpdateTask_Project_{uuid.uuid4().hex[:8]}",
            "project_type": "boh",
            "sale_price": 25000,
            "margin_goal_percent": 35,
            "is_test_data": True
        }
        
        project_response = self.session.post(f"{BASE_URL}/api/pm/projects", json=project_data)
        assert project_response.status_code == 200
        project = project_response.json()
        self.test_project_id = project["project_id"]
        
        # Create two milestones
        milestone1_response = self.session.post(
            f"{BASE_URL}/api/pm/projects/{self.test_project_id}/milestones",
            json={"name": "TEST_Phase 1", "order": 1}
        )
        milestone1 = milestone1_response.json()
        milestone1_id = milestone1["milestone_id"]
        
        milestone2_response = self.session.post(
            f"{BASE_URL}/api/pm/projects/{self.test_project_id}/milestones",
            json={"name": "TEST_Phase 2", "order": 2}
        )
        milestone2 = milestone2_response.json()
        milestone2_id = milestone2["milestone_id"]
        self.test_milestone_id = milestone2_id  # For cleanup
        
        # Get users for assignment
        users_response = self.session.get(f"{BASE_URL}/api/admin/users")
        users_data = users_response.json()
        users = users_data if isinstance(users_data, list) else users_data.get("items", [])
        goku = next((u for u in users if u.get("email") == "goku@simplimeta.io"), None)
        vegeta = next((u for u in users if u.get("email") == "vegeta@simplimeta.io"), None)
        
        # Create task with initial values
        task_data = {
            "name": "TEST_Task to Update",
            "milestone_id": milestone1_id,
            "assigned_to": goku["user_id"] if goku else None,
            "estimated_hours": 10.0,
            "actual_hours": 2.0
        }
        
        task_response = self.session.post(
            f"{BASE_URL}/api/pm/projects/{self.test_project_id}/tasks",
            json=task_data
        )
        assert task_response.status_code == 200
        task = task_response.json()
        self.test_task_id = task["task_id"]
        
        # Update task with new values
        update_data = {
            "milestone_id": milestone2_id,
            "assigned_to": vegeta["user_id"] if vegeta else goku["user_id"] if goku else None,
            "estimated_hours": 20.0,
            "actual_hours": 8.5
        }
        
        update_response = self.session.put(
            f"{BASE_URL}/api/pm/tasks/{self.test_task_id}",
            json=update_data
        )
        assert update_response.status_code == 200, f"Failed to update task: {update_response.text}"
        
        updated_task = update_response.json()
        
        # Verify updates
        assert updated_task.get("milestone_id") == milestone2_id, \
            f"Expected milestone_id={milestone2_id}, got {updated_task.get('milestone_id')}"
        assert updated_task.get("estimated_hours") == 20.0, \
            f"Expected estimated_hours=20.0, got {updated_task.get('estimated_hours')}"
        assert updated_task.get("actual_hours") == 8.5, \
            f"Expected actual_hours=8.5, got {updated_task.get('actual_hours')}"
        
        print(f"Updated task: milestone_id={updated_task.get('milestone_id')}, actual_hours={updated_task.get('actual_hours')}")
    
    # ==================== Integration Test ====================
    
    def test_full_workflow_create_project_milestone_task(self):
        """Test full workflow: Create project with PM -> Create milestone -> Create task with all fields"""
        # Get Goku for PM
        users_response = self.session.get(f"{BASE_URL}/api/admin/users")
        users_data = users_response.json()
        users = users_data if isinstance(users_data, list) else users_data.get("items", [])
        goku = next((u for u in users if u.get("email") == "goku@simplimeta.io"), None)
        
        if not goku:
            pytest.skip("Goku user not found")
        
        # Step 1: Create project with PM
        project_data = {
            "name": f"TEST_FullWorkflow_{uuid.uuid4().hex[:8]}",
            "description": "Full workflow test",
            "project_type": "boh",
            "sale_price": 100000,
            "margin_goal_percent": 35,
            "purchased_hours": 200,
            "project_manager_id": goku["user_id"],
            "is_test_data": True
        }
        
        project_response = self.session.post(f"{BASE_URL}/api/pm/projects", json=project_data)
        assert project_response.status_code == 200
        project = project_response.json()
        self.test_project_id = project["project_id"]
        
        assert "Goku" in project.get("project_manager_name", "")
        print(f"Step 1: Created project with PM {project.get('project_manager_name')}")
        
        # Step 2: Create milestone
        milestone_data = {
            "name": "TEST_Sprint 1",
            "description": "First sprint",
            "due_date": "2026-02-28",
            "order": 1
        }
        
        milestone_response = self.session.post(
            f"{BASE_URL}/api/pm/projects/{self.test_project_id}/milestones",
            json=milestone_data
        )
        assert milestone_response.status_code == 200
        milestone = milestone_response.json()
        self.test_milestone_id = milestone["milestone_id"]
        print(f"Step 2: Created milestone '{milestone.get('name')}'")
        
        # Step 3: Create task with all fields
        task_data = {
            "name": "TEST_Complete User Stories",
            "description": "Implement all user stories for Sprint 1",
            "milestone_id": self.test_milestone_id,
            "assigned_to": goku["user_id"],
            "estimated_hours": 40.0,
            "actual_hours": 12.5,
            "priority": "high",
            "due_date": "2026-02-20"
        }
        
        task_response = self.session.post(
            f"{BASE_URL}/api/pm/projects/{self.test_project_id}/tasks",
            json=task_data
        )
        assert task_response.status_code == 200
        task = task_response.json()
        self.test_task_id = task["task_id"]
        
        assert task.get("milestone_id") == self.test_milestone_id
        assert task.get("assigned_to") == goku["user_id"]
        assert task.get("estimated_hours") == 40.0
        assert task.get("actual_hours") == 12.5
        print(f"Step 3: Created task with milestone, assigned_to, estimated_hours=40, actual_hours=12.5")
        
        # Step 4: Verify project detail has task with milestone_name
        detail_response = self.session.get(f"{BASE_URL}/api/pm/projects/{self.test_project_id}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        
        tasks = detail.get("tasks", [])
        our_task = next((t for t in tasks if t.get("task_id") == self.test_task_id), None)
        
        assert our_task is not None
        assert our_task.get("milestone_name") == "TEST_Sprint 1"
        assert our_task.get("actual_hours") == 12.5
        print(f"Step 4: Verified task has milestone_name='{our_task.get('milestone_name')}' and actual_hours={our_task.get('actual_hours')}")
        
        print("Full workflow test PASSED!")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
