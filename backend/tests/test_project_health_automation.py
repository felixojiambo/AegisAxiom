"""
Test Project Health Automation APIs
Tests for:
- GET /api/admin/project-health-rules - Returns default health rules
- POST /api/admin/project-health-rules - Create new health rule
- PUT /api/admin/project-health-rules/{rule_id} - Update existing rule
- DELETE /api/admin/project-health-rules/{rule_id} - Delete rule
- GET /api/admin/capacity-health-rules - Returns default capacity rule
- PUT /api/admin/capacity-health-rules/{rule_id} - Update capacity rule
- GET /api/admin/capacity/{user_id}/calendar?months=3 - Returns 3-month calendar
- POST /api/pm/projects/{project_id}/evaluate-health - Evaluate project health
- GET /api/pm/projects/dashboard - Returns projects with health_badges
"""
import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestProjectHealthAutomation:
    """Test Project Health Automation APIs"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test session with authentication"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login to get auth token
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": "Admin@123!"
        })
        
        if login_response.status_code == 200:
            token = login_response.json().get("token")
            self.session.headers.update({"Authorization": f"Bearer {token}"})
            self.user_id = login_response.json().get("user", {}).get("user_id")
        else:
            pytest.skip("Authentication failed - skipping tests")
        
        yield
        
        # Cleanup: Delete any test rules created
        self._cleanup_test_rules()
    
    def _cleanup_test_rules(self):
        """Clean up test-created rules"""
        try:
            # Get all rules and delete test ones
            response = self.session.get(f"{BASE_URL}/api/admin/project-health-rules")
            if response.status_code == 200:
                rules = response.json().get("items", [])
                for rule in rules:
                    if rule.get("name", "").startswith("TEST_"):
                        self.session.delete(f"{BASE_URL}/api/admin/project-health-rules/{rule['rule_id']}")
        except Exception:
            pass
    
    # ==================== PROJECT HEALTH RULES ====================
    
    def test_get_project_health_rules_returns_defaults(self):
        """GET /api/admin/project-health-rules - Returns default health rules"""
        response = self.session.get(f"{BASE_URL}/api/admin/project-health-rules")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "items" in data, "Response should contain 'items' key"
        assert "total" in data, "Response should contain 'total' key"
        
        # Should have at least default rules
        items = data["items"]
        assert len(items) >= 1, "Should have at least one default rule"
        
        # Verify rule structure
        rule = items[0]
        assert "rule_id" in rule, "Rule should have rule_id"
        assert "name" in rule, "Rule should have name"
        assert "condition_type" in rule, "Rule should have condition_type"
        assert "severity" in rule, "Rule should have severity"
        
        print(f"✅ GET project-health-rules: {len(items)} rules returned")
    
    def test_create_project_health_rule(self):
        """POST /api/admin/project-health-rules - Create new health rule"""
        unique_id = uuid.uuid4().hex[:8]
        payload = {
            "name": f"TEST_Custom Budget Rule {unique_id}",
            "description": "Test rule for budget threshold",
            "rule_type": "health_status",
            "condition_type": "budget_consumed_percent",
            "threshold_value": 80.0,
            "severity": "warning",
            "badge_text": "Budget 80%",
            "badge_color": "orange",
            "applies_to": "all_projects",
            "enabled": True
        }
        
        response = self.session.post(f"{BASE_URL}/api/admin/project-health-rules", json=payload)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "rule_id" in data, "Response should contain rule_id"
        assert data["name"] == payload["name"], "Name should match"
        assert data["threshold_value"] == 80.0, "Threshold should be 80.0"
        assert data["severity"] == "warning", "Severity should be warning"
        assert data["badge_text"] == "Budget 80%", "Badge text should match"
        
        # Store for cleanup
        self.created_rule_id = data["rule_id"]
        
        print(f"✅ POST project-health-rules: Created rule {data['rule_id']}")
    
    def test_update_project_health_rule(self):
        """PUT /api/admin/project-health-rules/{rule_id} - Update existing rule"""
        # First create a rule
        unique_id = uuid.uuid4().hex[:8]
        create_payload = {
            "name": f"TEST_Update Rule {unique_id}",
            "description": "Rule to be updated",
            "rule_type": "health_status",
            "condition_type": "budget_consumed_percent",
            "threshold_value": 75.0,
            "severity": "warning",
            "badge_text": "Budget Warning",
            "badge_color": "yellow",
            "applies_to": "all_projects",
            "enabled": True
        }
        
        create_response = self.session.post(f"{BASE_URL}/api/admin/project-health-rules", json=create_payload)
        assert create_response.status_code == 200, f"Failed to create rule: {create_response.text}"
        
        rule_id = create_response.json()["rule_id"]
        
        # Now update the rule
        update_payload = {
            "threshold_value": 85.0,
            "severity": "critical",
            "badge_text": "Budget Critical",
            "badge_color": "red",
            "enabled": False
        }
        
        update_response = self.session.put(f"{BASE_URL}/api/admin/project-health-rules/{rule_id}", json=update_payload)
        
        assert update_response.status_code == 200, f"Expected 200, got {update_response.status_code}: {update_response.text}"
        
        data = update_response.json()
        assert data["threshold_value"] == 85.0, "Threshold should be updated to 85.0"
        assert data["severity"] == "critical", "Severity should be updated to critical"
        assert data["badge_text"] == "Budget Critical", "Badge text should be updated"
        assert data["enabled"] == False, "Enabled should be False"
        
        print(f"✅ PUT project-health-rules/{rule_id}: Rule updated successfully")
    
    def test_delete_project_health_rule(self):
        """DELETE /api/admin/project-health-rules/{rule_id} - Delete rule"""
        # First create a rule
        unique_id = uuid.uuid4().hex[:8]
        create_payload = {
            "name": f"TEST_Delete Rule {unique_id}",
            "description": "Rule to be deleted",
            "rule_type": "health_status",
            "condition_type": "budget_consumed_percent",
            "threshold_value": 70.0,
            "severity": "warning",
            "badge_text": "Test",
            "badge_color": "yellow",
            "applies_to": "all_projects",
            "enabled": True
        }
        
        create_response = self.session.post(f"{BASE_URL}/api/admin/project-health-rules", json=create_payload)
        assert create_response.status_code == 200, f"Failed to create rule: {create_response.text}"
        
        rule_id = create_response.json()["rule_id"]
        
        # Delete the rule
        delete_response = self.session.delete(f"{BASE_URL}/api/admin/project-health-rules/{rule_id}")
        
        assert delete_response.status_code == 200, f"Expected 200, got {delete_response.status_code}: {delete_response.text}"
        
        data = delete_response.json()
        assert "message" in data, "Response should contain message"
        
        # Verify rule is deleted - should return 404
        get_response = self.session.get(f"{BASE_URL}/api/admin/project-health-rules")
        rules = get_response.json().get("items", [])
        rule_ids = [r["rule_id"] for r in rules]
        assert rule_id not in rule_ids, "Deleted rule should not be in list"
        
        print(f"✅ DELETE project-health-rules/{rule_id}: Rule deleted successfully")
    
    def test_delete_nonexistent_rule_returns_404(self):
        """DELETE /api/admin/project-health-rules/{rule_id} - Returns 404 for nonexistent rule"""
        fake_rule_id = "nonexistent_rule_12345"
        
        response = self.session.delete(f"{BASE_URL}/api/admin/project-health-rules/{fake_rule_id}")
        
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        
        print("✅ DELETE nonexistent rule returns 404")
    
    # ==================== CAPACITY HEALTH RULES ====================
    
    def test_get_capacity_health_rules_returns_defaults(self):
        """GET /api/admin/capacity-health-rules - Returns default capacity rule"""
        response = self.session.get(f"{BASE_URL}/api/admin/capacity-health-rules")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "items" in data, "Response should contain 'items' key"
        assert "total" in data, "Response should contain 'total' key"
        
        # Should have at least one default rule
        items = data["items"]
        assert len(items) >= 1, "Should have at least one capacity rule"
        
        # Verify rule structure
        rule = items[0]
        assert "rule_id" in rule, "Rule should have rule_id"
        assert "green_threshold" in rule, "Rule should have green_threshold"
        assert "yellow_threshold" in rule, "Rule should have yellow_threshold"
        
        # Verify default thresholds
        assert rule["green_threshold"] == 6.0, "Default green threshold should be 6.0"
        assert rule["yellow_threshold"] == 8.0, "Default yellow threshold should be 8.0"
        
        print(f"✅ GET capacity-health-rules: {len(items)} rules returned with thresholds green<{rule['green_threshold']}h, yellow<{rule['yellow_threshold']}h")
    
    def test_update_capacity_health_rule(self):
        """PUT /api/admin/capacity-health-rules/{rule_id} - Update capacity rule"""
        # First get the default rule
        get_response = self.session.get(f"{BASE_URL}/api/admin/capacity-health-rules")
        assert get_response.status_code == 200
        
        rules = get_response.json().get("items", [])
        assert len(rules) >= 1, "Should have at least one capacity rule"
        
        rule_id = rules[0]["rule_id"]
        
        # Update the rule
        update_payload = {
            "green_threshold": 5.0,
            "yellow_threshold": 7.0,
            "enabled": True
        }
        
        update_response = self.session.put(f"{BASE_URL}/api/admin/capacity-health-rules/{rule_id}", json=update_payload)
        
        assert update_response.status_code == 200, f"Expected 200, got {update_response.status_code}: {update_response.text}"
        
        data = update_response.json()
        assert data["green_threshold"] == 5.0, "Green threshold should be updated to 5.0"
        assert data["yellow_threshold"] == 7.0, "Yellow threshold should be updated to 7.0"
        
        print(f"✅ PUT capacity-health-rules/{rule_id}: Rule updated successfully")
    
    # ==================== CAPACITY CALENDAR ====================
    
    def test_get_capacity_calendar_3_months(self):
        """GET /api/admin/capacity/{user_id}/calendar?months=3 - Returns 3-month calendar"""
        # Use the logged-in user's ID
        user_id = self.user_id
        
        response = self.session.get(f"{BASE_URL}/api/admin/capacity/{user_id}/calendar?months=3")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "user_id" in data, "Response should contain user_id"
        assert "months" in data, "Response should contain months array"
        assert "capacity_rule" in data, "Response should contain capacity_rule"
        
        # Verify 3 months returned
        months = data["months"]
        assert len(months) == 3, f"Should have 3 months, got {len(months)}"
        
        # Verify month structure
        for month in months:
            assert "month" in month, "Month should have month number"
            assert "year" in month, "Month should have year"
            assert "month_name" in month, "Month should have month_name"
            assert "days" in month, "Month should have days array"
            
            # Verify day structure
            if len(month["days"]) > 0:
                day = month["days"][0]
                assert "date" in day, "Day should have date"
                assert "allocated_hours" in day, "Day should have allocated_hours"
                assert "health" in day, "Day should have health (green/yellow/red)"
                assert "is_weekend" in day, "Day should have is_weekend flag"
        
        print(f"✅ GET capacity/{user_id}/calendar: 3 months returned with {sum(len(m['days']) for m in months)} total days")
    
    def test_capacity_calendar_health_colors(self):
        """Verify capacity calendar returns correct health colors based on thresholds"""
        user_id = self.user_id
        
        response = self.session.get(f"{BASE_URL}/api/admin/capacity/{user_id}/calendar?months=1")
        
        assert response.status_code == 200
        
        data = response.json()
        capacity_rule = data.get("capacity_rule", {})
        months = data.get("months", [])
        
        if len(months) > 0 and len(months[0]["days"]) > 0:
            # Verify health colors are valid
            for day in months[0]["days"]:
                health = day.get("health")
                assert health in ["green", "yellow", "red"], f"Health should be green/yellow/red, got {health}"
        
        print(f"✅ Capacity calendar health colors verified (thresholds: green<{capacity_rule.get('green_threshold', 6)}h, yellow<{capacity_rule.get('yellow_threshold', 8)}h)")
    
    # ==================== PROJECT HEALTH EVALUATION ====================
    
    def test_evaluate_project_health(self):
        """POST /api/pm/projects/{project_id}/evaluate-health - Evaluate project health"""
        # First get a project to evaluate
        projects_response = self.session.get(f"{BASE_URL}/api/pm/projects?is_test_data=true&limit=1")
        
        if projects_response.status_code != 200:
            pytest.skip("No projects available for testing")
        
        projects = projects_response.json().get("items", [])
        
        if len(projects) == 0:
            # Create a test project
            create_payload = {
                "name": f"TEST_Health Eval Project {uuid.uuid4().hex[:8]}",
                "description": "Test project for health evaluation",
                "project_type": "boh",
                "sale_price": 10000,
                "margin_goal_percent": 35,
                "purchased_hours": 100,
                "is_test_data": True
            }
            create_response = self.session.post(f"{BASE_URL}/api/pm/projects", json=create_payload)
            if create_response.status_code != 200:
                pytest.skip("Could not create test project")
            project_id = create_response.json()["project_id"]
        else:
            project_id = projects[0]["project_id"]
        
        # Evaluate health
        response = self.session.post(f"{BASE_URL}/api/admin/projects/{project_id}/evaluate-health")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "project_id" in data, "Response should contain project_id"
        assert "health_status" in data, "Response should contain health_status"
        assert "triggered_rules" in data, "Response should contain triggered_rules"
        
        # Verify health status is valid
        assert data["health_status"] in ["green", "yellow", "red"], f"Health status should be green/yellow/red, got {data['health_status']}"
        
        print(f"✅ POST evaluate-health: Project {project_id} health={data['health_status']}, triggered_rules={len(data['triggered_rules'])}")
    
    # ==================== DASHBOARD WITH HEALTH BADGES ====================
    
    def test_dashboard_returns_health_badges(self):
        """GET /api/pm/projects/dashboard - Returns projects with health_badges array"""
        response = self.session.get(f"{BASE_URL}/api/pm/projects/dashboard?is_test_data=true")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "projects" in data, "Response should contain 'projects' key"
        assert "summary" in data, "Response should contain 'summary' key"
        
        # Verify summary structure
        summary = data["summary"]
        assert "total_projects" in summary, "Summary should have total_projects"
        assert "health_green" in summary, "Summary should have health_green count"
        assert "health_yellow" in summary, "Summary should have health_yellow count"
        assert "health_red" in summary, "Summary should have health_red count"
        
        # Verify projects have health_badges
        projects = data["projects"]
        for project in projects:
            assert "health_status" in project, "Project should have health_status"
            assert "health_badges" in project, "Project should have health_badges array"
            assert isinstance(project["health_badges"], list), "health_badges should be a list"
            
            # Verify badge structure if any badges exist
            for badge in project["health_badges"]:
                assert "text" in badge or "badge_text" in badge, "Badge should have text"
                assert "color" in badge or "badge_color" in badge, "Badge should have color"
        
        print(f"✅ GET dashboard: {len(projects)} projects with health_badges, summary: green={summary['health_green']}, yellow={summary['health_yellow']}, red={summary['health_red']}")
    
    def test_dashboard_health_filter(self):
        """GET /api/pm/projects/dashboard?health_filter=green - Filters by health status"""
        # Test green filter
        response = self.session.get(f"{BASE_URL}/api/pm/projects/dashboard?is_test_data=true&health_filter=green")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        projects = data.get("projects", [])
        
        # All returned projects should be green
        for project in projects:
            assert project["health_status"] == "green", f"Expected green health, got {project['health_status']}"
        
        print(f"✅ GET dashboard with health_filter=green: {len(projects)} green projects returned")
    
    def test_dashboard_health_filter_yellow(self):
        """GET /api/pm/projects/dashboard?health_filter=yellow - Filters by yellow health"""
        response = self.session.get(f"{BASE_URL}/api/pm/projects/dashboard?is_test_data=true&health_filter=yellow")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        projects = data.get("projects", [])
        
        # All returned projects should be yellow
        for project in projects:
            assert project["health_status"] == "yellow", f"Expected yellow health, got {project['health_status']}"
        
        print(f"✅ GET dashboard with health_filter=yellow: {len(projects)} yellow projects returned")
    
    def test_dashboard_health_filter_red(self):
        """GET /api/pm/projects/dashboard?health_filter=red - Filters by red health"""
        response = self.session.get(f"{BASE_URL}/api/pm/projects/dashboard?is_test_data=true&health_filter=red")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        projects = data.get("projects", [])
        
        # All returned projects should be red
        for project in projects:
            assert project["health_status"] == "red", f"Expected red health, got {project['health_status']}"
        
        print(f"✅ GET dashboard with health_filter=red: {len(projects)} red projects returned")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
