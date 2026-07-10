"""
Test PM Custom Dashboards with configurable widgets.
Tests dashboard CRUD operations and all 15 widget data endpoints.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin@123!"


class TestPMDashboards:
    """Test PM Dashboard CRUD operations"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test session with authentication"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD
        })
        assert login_response.status_code == 200, f"Login failed: {login_response.text}"
        token = login_response.json().get("token")
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        
        yield
        
        # Cleanup: Delete any test dashboards created
        try:
            dashboards_res = self.session.get(f"{BASE_URL}/api/pm/dashboards")
            if dashboards_res.status_code == 200:
                dashboards = dashboards_res.json().get("items", [])
                for dash in dashboards:
                    if dash.get("name", "").startswith("TEST_"):
                        self.session.delete(f"{BASE_URL}/api/pm/dashboards/{dash['dashboard_id']}")
        except:
            pass
    
    def test_get_dashboards_returns_list(self):
        """GET /api/pm/dashboards returns list of dashboards (max 3)"""
        response = self.session.get(f"{BASE_URL}/api/pm/dashboards")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert data["total"] <= 3, "Should return max 3 dashboards"
        print(f"✅ GET /api/pm/dashboards - Found {data['total']} dashboards")
    
    def test_create_dashboard(self):
        """POST /api/pm/dashboards creates new dashboard"""
        # First check current count
        get_res = self.session.get(f"{BASE_URL}/api/pm/dashboards")
        current_count = get_res.json().get("total", 0)
        
        if current_count >= 3:
            # Delete one to make room for test
            dashboards = get_res.json().get("items", [])
            if dashboards:
                self.session.delete(f"{BASE_URL}/api/pm/dashboards/{dashboards[0]['dashboard_id']}")
        
        # Create new dashboard
        response = self.session.post(f"{BASE_URL}/api/pm/dashboards", json={
            "name": "TEST_Dashboard",
            "description": "Test dashboard for automated testing",
            "order": 0
        })
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "dashboard_id" in data
        assert data["name"] == "TEST_Dashboard"
        assert "widgets" in data
        assert isinstance(data["widgets"], list)
        print(f"✅ POST /api/pm/dashboards - Created dashboard: {data['dashboard_id']}")
        
        # Cleanup
        self.session.delete(f"{BASE_URL}/api/pm/dashboards/{data['dashboard_id']}")
    
    def test_dashboard_limit_enforced(self):
        """POST /api/pm/dashboards enforces 3 dashboard limit"""
        # Get current dashboards
        get_res = self.session.get(f"{BASE_URL}/api/pm/dashboards")
        current_count = get_res.json().get("total", 0)
        
        # Create dashboards until we hit 3
        created_ids = []
        for i in range(3 - current_count):
            res = self.session.post(f"{BASE_URL}/api/pm/dashboards", json={
                "name": f"TEST_Limit_{i}",
                "order": i
            })
            if res.status_code == 200:
                created_ids.append(res.json()["dashboard_id"])
        
        # Now try to create a 4th - should fail
        response = self.session.post(f"{BASE_URL}/api/pm/dashboards", json={
            "name": "TEST_Fourth",
            "order": 3
        })
        
        assert response.status_code == 400, f"Should fail with 400, got {response.status_code}"
        assert "Maximum 3 dashboards" in response.text
        print("✅ Dashboard limit (max 3) enforced correctly")
        
        # Cleanup
        for dash_id in created_ids:
            self.session.delete(f"{BASE_URL}/api/pm/dashboards/{dash_id}")
    
    def test_update_dashboard_widgets(self):
        """PUT /api/pm/dashboards/{id} updates dashboard widgets and layout"""
        # Create a dashboard first
        create_res = self.session.post(f"{BASE_URL}/api/pm/dashboards", json={
            "name": "TEST_Update",
            "order": 0
        })
        
        if create_res.status_code != 200:
            # May have hit limit, try to delete one
            get_res = self.session.get(f"{BASE_URL}/api/pm/dashboards")
            dashboards = get_res.json().get("items", [])
            if dashboards:
                self.session.delete(f"{BASE_URL}/api/pm/dashboards/{dashboards[0]['dashboard_id']}")
                create_res = self.session.post(f"{BASE_URL}/api/pm/dashboards", json={
                    "name": "TEST_Update",
                    "order": 0
                })
        
        assert create_res.status_code == 200, f"Failed to create: {create_res.text}"
        dashboard_id = create_res.json()["dashboard_id"]
        
        # Update with widgets
        widgets = [
            {
                "widget_id": "widget_test_1",
                "widget_type": "top_utilized_pms",
                "title": "Top Utilized PMs",
                "config": {},
                "layout": {"x": 0, "y": 0, "w": 4, "h": 3}
            },
            {
                "widget_id": "widget_test_2",
                "widget_type": "task_completion_rate",
                "title": "Task Completion Rate",
                "config": {},
                "layout": {"x": 4, "y": 0, "w": 4, "h": 3}
            }
        ]
        
        response = self.session.put(f"{BASE_URL}/api/pm/dashboards/{dashboard_id}", json={
            "widgets": widgets
        })
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert len(data["widgets"]) == 2
        assert data["widgets"][0]["widget_type"] == "top_utilized_pms"
        print(f"✅ PUT /api/pm/dashboards/{dashboard_id} - Updated with 2 widgets")
        
        # Cleanup
        self.session.delete(f"{BASE_URL}/api/pm/dashboards/{dashboard_id}")
    
    def test_delete_dashboard(self):
        """DELETE /api/pm/dashboards/{id} deletes dashboard"""
        # Create a dashboard first
        create_res = self.session.post(f"{BASE_URL}/api/pm/dashboards", json={
            "name": "TEST_Delete",
            "order": 0
        })
        
        if create_res.status_code != 200:
            get_res = self.session.get(f"{BASE_URL}/api/pm/dashboards")
            dashboards = get_res.json().get("items", [])
            if dashboards:
                self.session.delete(f"{BASE_URL}/api/pm/dashboards/{dashboards[0]['dashboard_id']}")
                create_res = self.session.post(f"{BASE_URL}/api/pm/dashboards", json={
                    "name": "TEST_Delete",
                    "order": 0
                })
        
        assert create_res.status_code == 200
        dashboard_id = create_res.json()["dashboard_id"]
        
        # Delete it
        response = self.session.delete(f"{BASE_URL}/api/pm/dashboards/{dashboard_id}")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        assert "deleted" in response.text.lower() or "message" in response.json()
        print(f"✅ DELETE /api/pm/dashboards/{dashboard_id} - Dashboard deleted")
        
        # Verify it's gone
        get_res = self.session.get(f"{BASE_URL}/api/pm/dashboards")
        dashboards = get_res.json().get("items", [])
        assert not any(d["dashboard_id"] == dashboard_id for d in dashboards)


class TestWidgetDataEndpoints:
    """Test all 15 widget data endpoints"""
    
    _shared_session = None
    _shared_token = None
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test session with authentication - reuse session to avoid rate limiting"""
        if TestWidgetDataEndpoints._shared_session is None:
            TestWidgetDataEndpoints._shared_session = requests.Session()
            TestWidgetDataEndpoints._shared_session.headers.update({"Content-Type": "application/json"})
            
            # Login once
            login_response = TestWidgetDataEndpoints._shared_session.post(f"{BASE_URL}/api/auth/login", json={
                "email": TEST_EMAIL,
                "password": TEST_PASSWORD
            })
            assert login_response.status_code == 200, f"Login failed: {login_response.text}"
            TestWidgetDataEndpoints._shared_token = login_response.json().get("token")
            TestWidgetDataEndpoints._shared_session.headers.update({"Authorization": f"Bearer {TestWidgetDataEndpoints._shared_token}"})
        
        self.session = TestWidgetDataEndpoints._shared_session
    
    def test_top_utilized_pms_widget(self):
        """GET /api/pm/widgets/top_utilized_pms/data returns PM stats with project counts"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/top_utilized_pms/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert data["widget_type"] == "top_utilized_pms"
        
        # Check data structure if there are PMs
        if data["data"]:
            pm = data["data"][0]
            assert "name" in pm
            assert "projects" in pm
            assert "total_hours" in pm
            assert "total_budget" in pm
        print(f"✅ top_utilized_pms - Found {len(data['data'])} PMs")
    
    def test_projects_by_health_widget(self):
        """GET /api/pm/widgets/projects_by_health/data returns health distribution"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/projects_by_health/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert data["widget_type"] == "projects_by_health"
        assert "chart_data" in data
        print(f"✅ projects_by_health - Health distribution: {data['data']}")
    
    def test_task_completion_rate_widget(self):
        """GET /api/pm/widgets/task_completion_rate/data returns completion stats"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/task_completion_rate/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert data["widget_type"] == "task_completion_rate"
        
        stats = data["data"]
        assert "total" in stats
        assert "completed" in stats
        assert "in_progress" in stats
        assert "completion_rate" in stats
        print(f"✅ task_completion_rate - {stats['completion_rate']}% completion rate")
    
    def test_capacity_overview_widget(self):
        """GET /api/pm/widgets/capacity_overview/data returns contractor capacity"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/capacity_overview/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert "summary" in data
        assert data["widget_type"] == "capacity_overview"
        
        summary = data["summary"]
        assert "available" in summary
        assert "busy" in summary
        assert "overallocated" in summary
        print(f"✅ capacity_overview - Available: {summary['available']}, Busy: {summary['busy']}, Overallocated: {summary['overallocated']}")
    
    def test_overdue_tasks_widget(self):
        """GET /api/pm/widgets/overdue_tasks/data returns overdue tasks list"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/overdue_tasks/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert "count" in data
        assert data["widget_type"] == "overdue_tasks"
        print(f"✅ overdue_tasks - Found {data['count']} overdue tasks")
    
    def test_top_billing_resources_widget(self):
        """GET /api/pm/widgets/top_billing_resources/data returns billing data"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/top_billing_resources/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert data["widget_type"] == "top_billing_resources"
        print(f"✅ top_billing_resources - Found {len(data['data'])} resources")
    
    def test_revenue_by_project_type_widget(self):
        """GET /api/pm/widgets/revenue_by_project_type/data returns revenue breakdown"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/revenue_by_project_type/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert data["widget_type"] == "revenue_by_project_type"
        print(f"✅ revenue_by_project_type - Found {len(data['data'])} project types")
    
    def test_budget_variance_widget(self):
        """GET /api/pm/widgets/budget_variance/data returns budget variance"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/budget_variance/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert data["widget_type"] == "budget_variance"
        print(f"✅ budget_variance - Found {len(data['data'])} projects with variance")
    
    def test_projects_by_status_widget(self):
        """GET /api/pm/widgets/projects_by_status/data returns status distribution"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/projects_by_status/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert data["widget_type"] == "projects_by_status"
        assert "chart_data" in data
        print(f"✅ projects_by_status - Status distribution: {data['data']}")
    
    def test_at_risk_projects_widget(self):
        """GET /api/pm/widgets/at_risk_projects/data returns at-risk projects"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/at_risk_projects/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert "count" in data
        assert data["widget_type"] == "at_risk_projects"
        print(f"✅ at_risk_projects - Found {data['count']} at-risk projects")
    
    def test_tasks_by_status_widget(self):
        """GET /api/pm/widgets/tasks_by_status/data returns task status distribution"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/tasks_by_status/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert data["widget_type"] == "tasks_by_status"
        assert "chart_data" in data
        print(f"✅ tasks_by_status - Status distribution: {data['data']}")
    
    def test_tasks_by_priority_widget(self):
        """GET /api/pm/widgets/tasks_by_priority/data returns task priority distribution"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/tasks_by_priority/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert data["widget_type"] == "tasks_by_priority"
        assert "chart_data" in data
        print(f"✅ tasks_by_priority - Priority distribution: {data['data']}")
    
    def test_over_allocated_resources_widget(self):
        """GET /api/pm/widgets/over_allocated_resources/data returns over-allocated resources"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/over_allocated_resources/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert "count" in data
        assert data["widget_type"] == "over_allocated_resources"
        print(f"✅ over_allocated_resources - Found {data['count']} over-allocated resources")
    
    def test_hours_by_resource_widget(self):
        """GET /api/pm/widgets/hours_by_resource/data returns hours by resource"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/hours_by_resource/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert data["widget_type"] == "hours_by_resource"
        print(f"✅ hours_by_resource - Found {len(data['data'])} resources with hours")
    
    def test_hours_variance_widget(self):
        """GET /api/pm/widgets/hours_variance/data returns hours variance"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/hours_variance/data")
        
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert data["widget_type"] == "hours_variance"
        print(f"✅ hours_variance - Found {len(data['data'])} tasks with variance")
    
    def test_invalid_widget_type_returns_404(self):
        """GET /api/pm/widgets/invalid_type/data returns 404"""
        response = self.session.get(f"{BASE_URL}/api/pm/widgets/invalid_widget_type/data")
        
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("✅ Invalid widget type correctly returns 404")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
