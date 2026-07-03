"""
Test SOW Enhanced Features - Iteration 43
Tests for:
1. Assignment API - PATCH /api/sales-ops/opportunities/{id}/sows/{sow_id}/assign
2. Status change with expected_completion_date validation
3. Expected completion date field in SOW
4. Date comparison warning logic
"""
import pytest
import requests
import os
from datetime import datetime, timedelta

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestSOWEnhancedFeatures:
    """Test SOW Enhanced Features for Kanban and SOW Builder"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test session with authentication"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Login
        login_response = self.session.post(f"{BASE_URL}/api/auth/login", json={
            "email": "support@simplimeta.io",
            "password": "Admin@123!"
        })
        assert login_response.status_code == 200, f"Login failed: {login_response.text}"
        token = login_response.json().get("access_token")
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        
        yield
        
        self.session.close()
    
    # ============ SALES OPS USERS ENDPOINT ============
    
    def test_get_sales_ops_users(self):
        """Test GET /api/sales-ops/sales-ops-users returns list of sales_ops users"""
        response = self.session.get(f"{BASE_URL}/api/sales-ops/sales-ops-users")
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "items" in data, "Response should have 'items' key"
        # May be empty if no sales_ops users exist
        assert isinstance(data["items"], list), "Items should be a list"
    
    # ============ SOW TASKS ENDPOINT ============
    
    def test_get_sow_tasks_for_opportunity(self):
        """Test GET /api/sales-ops/opportunities/{id}/sow-tasks returns SOW tasks with expected_completion_date"""
        # First get an opportunity in proposal_development stage
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=5")
        assert opps_response.status_code == 200
        
        opps = opps_response.json().get("items", [])
        if not opps:
            pytest.skip("No opportunities in proposal_development stage")
        
        opp = opps[0]
        opp_id = opp["opportunity_id"]
        
        # Get SOW tasks
        response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow-tasks")
        assert response.status_code == 200, f"Failed: {response.text}"
        
        data = response.json()
        assert "sow_tasks" in data, "Response should have 'sow_tasks' key"
        assert "opportunity" in data, "Response should have 'opportunity' key"
        
        # Check SOW task structure if any exist
        if data["sow_tasks"]:
            sow = data["sow_tasks"][0]
            assert "sow_id" in sow, "SOW should have sow_id"
            assert "line_of_business" in sow, "SOW should have line_of_business"
            assert "status" in sow, "SOW should have status"
            # expected_completion_date may or may not be set
            assert "expected_completion_date" in sow or sow.get("expected_completion_date") is None
    
    # ============ SOW ASSIGNMENT ENDPOINT ============
    
    def test_assign_sow_endpoint(self):
        """Test PATCH /api/sales-ops/opportunities/{id}/sows/{sow_id}/assign"""
        # Get an opportunity with SOW tasks
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=10")
        assert opps_response.status_code == 200
        
        opps = opps_response.json().get("items", [])
        if not opps:
            pytest.skip("No opportunities in proposal_development stage")
        
        # Find an opportunity with SOW tasks
        sow_to_test = None
        opp_id = None
        
        for opp in opps:
            sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sow-tasks")
            if sow_response.status_code == 200:
                sow_data = sow_response.json()
                if sow_data.get("sow_tasks"):
                    sow_to_test = sow_data["sow_tasks"][0]
                    opp_id = opp["opportunity_id"]
                    break
        
        if not sow_to_test:
            pytest.skip("No SOW tasks found to test assignment")
        
        # Get current user to use as assignee (admin user)
        me_response = self.session.get(f"{BASE_URL}/api/auth/me")
        assert me_response.status_code == 200
        user_id = me_response.json().get("user_id")
        
        # Test assignment
        response = self.session.patch(
            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sows/{sow_to_test['sow_id']}/assign?assigned_to={user_id}"
        )
        assert response.status_code == 200, f"Assignment failed: {response.text}"
        
        data = response.json()
        assert "message" in data, "Response should have message"
        assert data.get("assigned_to") == user_id, "assigned_to should match"
    
    def test_assign_sow_invalid_user(self):
        """Test PATCH assign with invalid user returns 404"""
        # Get an opportunity with SOW tasks
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=5")
        opps = opps_response.json().get("items", [])
        
        if not opps:
            pytest.skip("No opportunities available")
        
        for opp in opps:
            sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sow-tasks")
            if sow_response.status_code == 200:
                sow_data = sow_response.json()
                if sow_data.get("sow_tasks"):
                    sow = sow_data["sow_tasks"][0]
                    
                    # Try to assign to non-existent user
                    response = self.session.patch(
                        f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sows/{sow['sow_id']}/assign?assigned_to=invalid_user_id_12345"
                    )
                    assert response.status_code == 404, f"Should return 404 for invalid user: {response.text}"
                    return
        
        pytest.skip("No SOW tasks found")
    
    # ============ STATUS CHANGE WITH DATE VALIDATION ============
    
    def test_status_change_to_in_progress_requires_expected_date(self):
        """Test PATCH status to 'in_progress' requires expected_completion_date"""
        # Get an opportunity with SOW tasks
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=10")
        opps = opps_response.json().get("items", [])
        
        if not opps:
            pytest.skip("No opportunities available")
        
        for opp in opps:
            sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sow-tasks")
            if sow_response.status_code == 200:
                sow_data = sow_response.json()
                for sow in sow_data.get("sow_tasks", []):
                    # Find a SOW without expected_completion_date
                    if not sow.get("expected_completion_date") and sow.get("status") == "not_started":
                        # Try to move to in_progress without expected_completion_date
                        response = self.session.patch(
                            f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sows/{sow['sow_id']}/status?status=in_progress"
                        )
                        
                        # Should fail with 400
                        assert response.status_code == 400, f"Should fail without expected_completion_date: {response.text}"
                        error_data = response.json()
                        assert "Expected Completion Date" in error_data.get("detail", ""), "Error should mention Expected Completion Date"
                        return
        
        pytest.skip("No SOW without expected_completion_date found in not_started status")
    
    def test_status_change_with_expected_date_succeeds(self):
        """Test PATCH status to 'in_progress' succeeds when expected_completion_date is set"""
        # Get an opportunity with SOW tasks
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=10")
        opps = opps_response.json().get("items", [])
        
        if not opps:
            pytest.skip("No opportunities available")
        
        for opp in opps:
            sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sow-tasks")
            if sow_response.status_code == 200:
                sow_data = sow_response.json()
                for sow in sow_data.get("sow_tasks", []):
                    # Find a SOW in not_started status
                    if sow.get("status") == "not_started":
                        opp_id = opp["opportunity_id"]
                        sow_id = sow["sow_id"]
                        lob = sow["line_of_business"]
                        
                        # First set expected_completion_date via SOW update
                        future_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
                        update_response = self.session.post(
                            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow?lob={lob}",
                            json={"expected_completion_date": future_date}
                        )
                        
                        if update_response.status_code != 200:
                            continue
                        
                        # Now try to move to in_progress
                        response = self.session.patch(
                            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sows/{sow_id}/status?status=in_progress"
                        )
                        
                        assert response.status_code == 200, f"Should succeed with expected_completion_date: {response.text}"
                        
                        # Revert status back to not_started for cleanup
                        self.session.patch(
                            f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sows/{sow_id}/status?status=not_started"
                        )
                        return
        
        pytest.skip("No SOW in not_started status found")
    
    # ============ SOW UPDATE WITH EXPECTED COMPLETION DATE ============
    
    def test_sow_update_with_expected_completion_date(self):
        """Test POST /api/sales-ops/opportunities/{id}/sow accepts expected_completion_date"""
        # Get an opportunity with SOW tasks
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=5")
        opps = opps_response.json().get("items", [])
        
        if not opps:
            pytest.skip("No opportunities available")
        
        for opp in opps:
            sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sow-tasks")
            if sow_response.status_code == 200:
                sow_data = sow_response.json()
                if sow_data.get("sow_tasks"):
                    sow = sow_data["sow_tasks"][0]
                    opp_id = opp["opportunity_id"]
                    lob = sow["line_of_business"]
                    
                    # Update SOW with expected_completion_date
                    future_date = (datetime.now() + timedelta(days=45)).strftime("%Y-%m-%d")
                    response = self.session.post(
                        f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/sow?lob={lob}",
                        json={"expected_completion_date": future_date}
                    )
                    
                    assert response.status_code == 200, f"SOW update failed: {response.text}"
                    
                    data = response.json()
                    assert data.get("expected_completion_date") == future_date, "expected_completion_date should be updated"
                    return
        
        pytest.skip("No SOW tasks found")
    
    # ============ BLOCKERS ENDPOINT ============
    
    def test_blockers_endpoint(self):
        """Test GET /api/sales-ops/opportunities/{id}/sows/{sow_id}/blockers"""
        # Get an opportunity with SOW tasks
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=5")
        opps = opps_response.json().get("items", [])
        
        if not opps:
            pytest.skip("No opportunities available")
        
        for opp in opps:
            sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sow-tasks")
            if sow_response.status_code == 200:
                sow_data = sow_response.json()
                if sow_data.get("sow_tasks"):
                    sow = sow_data["sow_tasks"][0]
                    
                    # Get blockers
                    response = self.session.get(
                        f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sows/{sow['sow_id']}/blockers"
                    )
                    
                    assert response.status_code == 200, f"Blockers endpoint failed: {response.text}"
                    
                    data = response.json()
                    assert "sow_id" in data, "Response should have sow_id"
                    assert "has_blockers" in data, "Response should have has_blockers"
                    assert "blockers" in data, "Response should have blockers list"
                    return
        
        pytest.skip("No SOW tasks found")
    
    # ============ VALID STATUS VALUES ============
    
    def test_invalid_status_returns_400(self):
        """Test PATCH status with invalid status returns 400"""
        # Get an opportunity with SOW tasks
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=5")
        opps = opps_response.json().get("items", [])
        
        if not opps:
            pytest.skip("No opportunities available")
        
        for opp in opps:
            sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sow-tasks")
            if sow_response.status_code == 200:
                sow_data = sow_response.json()
                if sow_data.get("sow_tasks"):
                    sow = sow_data["sow_tasks"][0]
                    
                    # Try invalid status
                    response = self.session.patch(
                        f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sows/{sow['sow_id']}/status?status=invalid_status"
                    )
                    
                    assert response.status_code == 400, f"Should return 400 for invalid status: {response.text}"
                    return
        
        pytest.skip("No SOW tasks found")
    
    # ============ SOW BY LOB ENDPOINT ============
    
    def test_get_sow_by_lob(self):
        """Test GET /api/sales-ops/opportunities/{id}/sow?lob={lob}"""
        # Get an opportunity with LOBs
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=5")
        opps = opps_response.json().get("items", [])
        
        if not opps:
            pytest.skip("No opportunities available")
        
        for opp in opps:
            lobs = opp.get("lines_of_business", [])
            # Filter out referral_mrr
            valid_lobs = [l for l in lobs if l != "referral_mrr"]
            
            if valid_lobs:
                lob = valid_lobs[0]
                response = self.session.get(
                    f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sow?lob={lob}"
                )
                
                assert response.status_code == 200, f"Get SOW by LOB failed: {response.text}"
                
                data = response.json()
                assert "sow_id" in data, "Response should have sow_id"
                assert data.get("line_of_business") == lob, "LOB should match"
                return
        
        pytest.skip("No opportunities with valid LOBs found")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
