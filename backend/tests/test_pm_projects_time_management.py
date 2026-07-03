"""
Test PM Projects and Time Management Features
- PM Projects CRUD with project types (BOH, BOA, FF, Hybrid)
- Task and Milestone management
- Capacity planning (overall + project-specific)
- Time locking rules configuration
- 10-minute increment validation
- Project health calculation
"""
import pytest
import requests
import os
from datetime import datetime, timedelta

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin@123!"


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token."""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })
    if response.status_code == 200:
        data = response.json()
        return data.get("token")
    pytest.skip(f"Authentication failed: {response.status_code} - {response.text}")


@pytest.fixture(scope="module")
def api_client(auth_token):
    """Session with auth header."""
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_token}"
    })
    return session


class TestPMProjectsCRUD:
    """Test PM Projects CRUD operations."""
    
    created_project_id = None
    
    def test_create_pm_project_boh(self, api_client):
        """Create a BOH project with sale price and margin calculation."""
        payload = {
            "name": "TEST_PM_Project_BOH",
            "description": "Test BOH project for testing",
            "project_type": "boh",
            "sale_price": 100000,
            "margin_goal_percent": 35,
            "purchased_hours": 500,
            "start_date": "2026-01-15",
            "target_completion_date": "2026-06-30",
            "is_test_data": True
        }
        
        response = api_client.post(f"{BASE_URL}/api/pm/projects", json=payload)
        assert response.status_code == 200, f"Failed to create project: {response.text}"
        
        data = response.json()
        assert data["name"] == "TEST_PM_Project_BOH"
        assert data["project_type"] == "boh"
        assert data["sale_price"] == 100000
        assert data["margin_goal_percent"] == 35
        # Verify cost_budget calculation: sale_price * (1 - margin/100) = 100000 * 0.65 = 65000
        assert data["cost_budget"] == 65000, f"Expected cost_budget 65000, got {data['cost_budget']}"
        assert data["purchased_hours"] == 500
        assert data["health_status"] == "green"
        
        TestPMProjectsCRUD.created_project_id = data["project_id"]
        print(f"✓ Created BOH project: {data['project_id']}")
    
    def test_create_pm_project_fixed_fee(self, api_client):
        """Create a Fixed Fee project."""
        payload = {
            "name": "TEST_PM_Project_FF",
            "description": "Test Fixed Fee project",
            "project_type": "fixed_fee",
            "sale_price": 50000,
            "margin_goal_percent": 35,
            "start_date": "2026-02-01",
            "is_test_data": True
        }
        
        response = api_client.post(f"{BASE_URL}/api/pm/projects", json=payload)
        assert response.status_code == 200, f"Failed to create FF project: {response.text}"
        
        data = response.json()
        assert data["project_type"] == "fixed_fee"
        assert data["cost_budget"] == 32500  # 50000 * 0.65
        print(f"✓ Created Fixed Fee project: {data['project_id']}")
    
    def test_create_pm_project_hybrid(self, api_client):
        """Create a Hybrid project."""
        payload = {
            "name": "TEST_PM_Project_Hybrid",
            "description": "Test Hybrid project",
            "project_type": "hybrid",
            "sale_price": 75000,
            "margin_goal_percent": 35,
            "purchased_hours": 200,
            "is_test_data": True
        }
        
        response = api_client.post(f"{BASE_URL}/api/pm/projects", json=payload)
        assert response.status_code == 200, f"Failed to create Hybrid project: {response.text}"
        
        data = response.json()
        assert data["project_type"] == "hybrid"
        print(f"✓ Created Hybrid project: {data['project_id']}")
    
    def test_get_pm_projects_dashboard(self, api_client):
        """Get PM projects dashboard with summary."""
        response = api_client.get(f"{BASE_URL}/api/pm/projects/dashboard?is_test_data=true")
        assert response.status_code == 200, f"Failed to get dashboard: {response.text}"
        
        data = response.json()
        assert "projects" in data
        assert "summary" in data
        assert "total_projects" in data["summary"]
        assert "health_green" in data["summary"]
        assert "health_yellow" in data["summary"]
        assert "health_red" in data["summary"]
        
        print(f"✓ Dashboard: {data['summary']['total_projects']} projects, {data['summary']['health_green']} green")
    
    def test_get_pm_project_detail(self, api_client):
        """Get detailed project info with tasks and milestones."""
        if not TestPMProjectsCRUD.created_project_id:
            pytest.skip("No project created")
        
        response = api_client.get(f"{BASE_URL}/api/pm/projects/{TestPMProjectsCRUD.created_project_id}")
        assert response.status_code == 200, f"Failed to get project: {response.text}"
        
        data = response.json()
        assert data["project_id"] == TestPMProjectsCRUD.created_project_id
        assert "tasks" in data
        assert "milestones" in data
        assert "time_summary" in data
        assert "health_status" in data
        assert "remaining_hours" in data
        
        print(f"✓ Project detail: {data['name']}, health={data['health_status']}")
    
    def test_update_pm_project(self, api_client):
        """Update project details."""
        if not TestPMProjectsCRUD.created_project_id:
            pytest.skip("No project created")
        
        payload = {
            "status": "active",
            "sale_price": 120000
        }
        
        response = api_client.put(
            f"{BASE_URL}/api/pm/projects/{TestPMProjectsCRUD.created_project_id}",
            json=payload
        )
        assert response.status_code == 200, f"Failed to update project: {response.text}"
        
        data = response.json()
        assert data["status"] == "active"
        assert data["sale_price"] == 120000
        # Verify cost_budget recalculated: 120000 * 0.65 = 78000
        assert data["cost_budget"] == 78000, f"Expected cost_budget 78000, got {data['cost_budget']}"
        
        print(f"✓ Updated project: status={data['status']}, cost_budget={data['cost_budget']}")


class TestMilestones:
    """Test milestone CRUD operations."""
    
    created_milestone_id = None
    
    def test_create_milestone(self, api_client):
        """Create a milestone for a project."""
        if not TestPMProjectsCRUD.created_project_id:
            pytest.skip("No project created")
        
        payload = {
            "name": "Phase 1: Setup",
            "description": "Initial project setup and configuration",
            "due_date": "2026-02-15",
            "order": 1
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/pm/projects/{TestPMProjectsCRUD.created_project_id}/milestones",
            json=payload
        )
        assert response.status_code == 200, f"Failed to create milestone: {response.text}"
        
        data = response.json()
        assert data["name"] == "Phase 1: Setup"
        assert data["status"] == "not_started"
        assert data["order"] == 1
        
        TestMilestones.created_milestone_id = data["milestone_id"]
        print(f"✓ Created milestone: {data['milestone_id']}")
    
    def test_get_milestones(self, api_client):
        """Get all milestones for a project."""
        if not TestPMProjectsCRUD.created_project_id:
            pytest.skip("No project created")
        
        response = api_client.get(
            f"{BASE_URL}/api/pm/projects/{TestPMProjectsCRUD.created_project_id}/milestones"
        )
        assert response.status_code == 200, f"Failed to get milestones: {response.text}"
        
        data = response.json()
        assert "items" in data
        assert "total" in data
        print(f"✓ Got {data['total']} milestones")
    
    def test_update_milestone(self, api_client):
        """Update milestone status."""
        if not TestMilestones.created_milestone_id:
            pytest.skip("No milestone created")
        
        payload = {
            "status": "in_progress"
        }
        
        response = api_client.put(
            f"{BASE_URL}/api/pm/milestones/{TestMilestones.created_milestone_id}",
            json=payload
        )
        assert response.status_code == 200, f"Failed to update milestone: {response.text}"
        
        data = response.json()
        assert data["status"] == "in_progress"
        print(f"✓ Updated milestone status to: {data['status']}")


class TestTasks:
    """Test task CRUD operations."""
    
    created_task_id = None
    
    def test_create_task_with_estimated_hours(self, api_client):
        """Create a task with required estimated hours field."""
        if not TestPMProjectsCRUD.created_project_id:
            pytest.skip("No project created")
        
        payload = {
            "name": "Setup Development Environment",
            "description": "Install dependencies and configure local environment",
            "estimated_hours": 8.0,
            "priority": "high",
            "status": "not_started",
            "milestone_id": TestMilestones.created_milestone_id
        }
        
        response = api_client.post(
            f"{BASE_URL}/api/pm/projects/{TestPMProjectsCRUD.created_project_id}/tasks",
            json=payload
        )
        assert response.status_code == 200, f"Failed to create task: {response.text}"
        
        data = response.json()
        assert data["name"] == "Setup Development Environment"
        assert data["estimated_hours"] == 8.0
        assert data["priority"] == "high"
        assert data["status"] == "not_started"
        
        TestTasks.created_task_id = data["task_id"]
        print(f"✓ Created task: {data['task_id']} with {data['estimated_hours']}h estimated")
    
    def test_get_tasks(self, api_client):
        """Get all tasks for a project."""
        if not TestPMProjectsCRUD.created_project_id:
            pytest.skip("No project created")
        
        response = api_client.get(
            f"{BASE_URL}/api/pm/projects/{TestPMProjectsCRUD.created_project_id}/tasks"
        )
        assert response.status_code == 200, f"Failed to get tasks: {response.text}"
        
        data = response.json()
        assert "items" in data
        assert "total" in data
        print(f"✓ Got {data['total']} tasks")
    
    def test_update_task_status(self, api_client):
        """Update task status."""
        if not TestTasks.created_task_id:
            pytest.skip("No task created")
        
        payload = {
            "status": "in_progress"
        }
        
        response = api_client.put(
            f"{BASE_URL}/api/pm/tasks/{TestTasks.created_task_id}",
            json=payload
        )
        assert response.status_code == 200, f"Failed to update task: {response.text}"
        
        data = response.json()
        assert data["status"] == "in_progress"
        print(f"✓ Updated task status to: {data['status']}")


class TestTaskImport:
    """Test task import functionality."""
    
    def test_get_import_template(self, api_client):
        """Download task import template."""
        response = api_client.get(f"{BASE_URL}/api/pm/tasks/import-template")
        assert response.status_code == 200, f"Failed to get template: {response.text}"
        
        data = response.json()
        assert "template_url" in data
        assert "filename" in data
        assert "instructions" in data
        assert data["filename"] == "task_import_template.csv"
        
        print(f"✓ Got import template: {data['filename']}")


class TestCapacityPlanning:
    """Test capacity planning views."""
    
    def test_capacity_overview(self, api_client):
        """Get overall capacity view for all engineers/PMs."""
        response = api_client.get(f"{BASE_URL}/api/pm/capacity/overview")
        assert response.status_code == 200, f"Failed to get capacity overview: {response.text}"
        
        data = response.json()
        assert "capacity" in data
        assert "summary" in data
        assert "total_users" in data["summary"]
        assert "available" in data["summary"]
        assert "at_capacity" in data["summary"]
        assert "overallocated" in data["summary"]
        
        print(f"✓ Capacity overview: {data['summary']['total_users']} users, {data['summary']['available']} available")
    
    def test_project_capacity(self, api_client):
        """Get capacity view for a specific project."""
        if not TestPMProjectsCRUD.created_project_id:
            pytest.skip("No project created")
        
        response = api_client.get(
            f"{BASE_URL}/api/pm/projects/{TestPMProjectsCRUD.created_project_id}/capacity"
        )
        assert response.status_code == 200, f"Failed to get project capacity: {response.text}"
        
        data = response.json()
        assert "project_id" in data
        assert "project_name" in data
        assert "capacity" in data
        
        print(f"✓ Project capacity: {len(data['capacity'])} team members")


class TestTimeLockingRules:
    """Test time locking automation rules."""
    
    def test_get_time_locking_rules(self, api_client):
        """Get all time locking rules."""
        response = api_client.get(f"{BASE_URL}/api/admin/time-locking/rules")
        assert response.status_code == 200, f"Failed to get rules: {response.text}"
        
        data = response.json()
        assert "items" in data
        assert "total" in data
        
        # Should have at least the default rule
        if data["total"] > 0:
            rule = data["items"][0]
            assert "rule_id" in rule
            assert "lock_after_days" in rule
            assert "lock_time_hour" in rule
            assert "lock_time_minute" in rule
            assert "enabled" in rule
            print(f"✓ Got {data['total']} time locking rules")
        else:
            print("✓ No custom rules, using default")
    
    def test_get_active_time_locking_rule(self, api_client):
        """Get the currently active time locking rule."""
        response = api_client.get(f"{BASE_URL}/api/admin/time-locking/rules/active")
        assert response.status_code == 200, f"Failed to get active rule: {response.text}"
        
        data = response.json()
        assert "lock_after_days" in data
        assert "lock_time_hour" in data
        assert "lock_time_minute" in data
        
        print(f"✓ Active rule: lock after {data['lock_after_days']} days at {data['lock_time_hour']}:{data['lock_time_minute']:02d} UTC")


class TestTimeEntryValidation:
    """Test time entry validation with 10-minute increments."""
    
    def test_valid_10_minute_increments(self, api_client):
        """Test that valid 10-minute increments are accepted."""
        # Get a project to use
        response = api_client.get(f"{BASE_URL}/api/projects?limit=1")
        if response.status_code != 200 or not response.json().get("items"):
            pytest.skip("No projects available for time entry test")
        
        project_id = response.json()["items"][0]["project_id"]
        
        # Valid 10-minute increments: 0.17, 0.33, 0.50, 0.67, 0.83, 1.0
        valid_hours = [0.17, 0.33, 0.5, 0.67, 0.83, 1.0, 1.5, 2.0]
        
        for hours in valid_hours[:2]:  # Test first 2 to avoid creating too many entries
            payload = {
                "project_id": project_id,
                "date": "2026-01-15",
                "hours": hours,
                "notes": f"Test entry for {hours} hours - 10 minute increment validation",
                "is_billable": True,
                "is_test_data": True
            }
            
            response = api_client.post(f"{BASE_URL}/api/time-entries", json=payload)
            # May fail due to week lock, but should not fail due to increment validation
            if response.status_code == 400:
                error = response.json().get("detail", "")
                assert "10-minute increments" not in error, f"Valid increment {hours} was rejected"
            
        print(f"✓ Valid 10-minute increments accepted")
    
    def test_max_4_hours_per_entry(self, api_client):
        """Test that entries over 4 hours are rejected."""
        response = api_client.get(f"{BASE_URL}/api/projects?limit=1")
        if response.status_code != 200 or not response.json().get("items"):
            pytest.skip("No projects available for time entry test")
        
        project_id = response.json()["items"][0]["project_id"]
        
        payload = {
            "project_id": project_id,
            "date": "2026-01-15",
            "hours": 5.0,  # Over 4 hours
            "notes": "Test entry over 4 hours",
            "is_billable": True,
            "is_test_data": True
        }
        
        response = api_client.post(f"{BASE_URL}/api/time-entries", json=payload)
        assert response.status_code == 400, f"Expected 400 for >4 hours, got {response.status_code}"
        
        error = response.json().get("detail", "")
        assert "4 hours" in error.lower() or "maximum" in error.lower(), f"Expected max hours error, got: {error}"
        
        print(f"✓ Max 4 hours per entry enforced")


class TestProjectHealth:
    """Test project health calculation."""
    
    def test_health_status_calculation(self, api_client):
        """Verify health status is calculated correctly."""
        # Create a project with specific hours to test health calculation
        payload = {
            "name": "TEST_Health_Calculation",
            "project_type": "boh",
            "sale_price": 10000,
            "margin_goal_percent": 35,
            "purchased_hours": 100,
            "is_test_data": True
        }
        
        response = api_client.post(f"{BASE_URL}/api/pm/projects", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        # New project with 0 consumed hours should be green
        assert data["health_status"] == "green", f"Expected green health, got {data['health_status']}"
        
        print(f"✓ Health status calculation: {data['health_status']} (0% consumed)")


class TestDocuments:
    """Test document upload functionality."""
    
    def test_get_project_documents(self, api_client):
        """Get documents for a project."""
        if not TestPMProjectsCRUD.created_project_id:
            pytest.skip("No project created")
        
        response = api_client.get(
            f"{BASE_URL}/api/pm/projects/{TestPMProjectsCRUD.created_project_id}/documents"
        )
        assert response.status_code == 200, f"Failed to get documents: {response.text}"
        
        data = response.json()
        assert "documents" in data
        print(f"✓ Got {len(data['documents'])} documents")


class TestCleanup:
    """Cleanup test data."""
    
    def test_cleanup_test_projects(self, api_client):
        """Delete test projects."""
        # Get all test projects
        response = api_client.get(f"{BASE_URL}/api/pm/projects?is_test_data=true")
        if response.status_code != 200:
            pytest.skip("Could not get test projects")
        
        projects = response.json().get("items", [])
        deleted = 0
        
        for project in projects:
            if project.get("name", "").startswith("TEST_"):
                del_response = api_client.delete(f"{BASE_URL}/api/pm/projects/{project['project_id']}")
                if del_response.status_code == 200:
                    deleted += 1
        
        print(f"✓ Cleaned up {deleted} test projects")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
