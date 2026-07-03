"""
Test Automation Rules API - Sales Operations Automations
Tests CRUD operations for automation rules and alerts endpoints
"""
import pytest
import requests
import os
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestAutomationRulesAPI:
    """Test automation rules CRUD endpoints"""
    
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
            data = login_response.json()
            token = data.get("access_token") or data.get("token")
            if token:
                self.session.headers.update({"Authorization": f"Bearer {token}"})
        
        self.created_rule_ids = []
        yield
        
        # Cleanup: Delete test-created rules
        for rule_id in self.created_rule_ids:
            try:
                self.session.delete(f"{BASE_URL}/api/sales-ops/automation-rules/{rule_id}")
            except:
                pass
    
    def test_get_automation_rules_list(self):
        """Test GET /api/sales-ops/automation-rules - List all rules"""
        response = self.session.get(f"{BASE_URL}/api/sales-ops/automation-rules")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "items" in data, "Response should contain 'items' key"
        assert "total" in data, "Response should contain 'total' key"
        assert isinstance(data["items"], list), "Items should be a list"
        print(f"✅ GET automation rules: {data['total']} rules found")
    
    def test_create_automation_rule(self):
        """Test POST /api/sales-ops/automation-rules - Create new rule"""
        rule_payload = {
            "name": "TEST_High Value Alert",
            "description": "Alert when opportunity value exceeds threshold",
            "trigger_category": "size",
            "trigger_type": "value_above",
            "conditions": [
                {"field": "opportunity_value", "operator": "greater_than", "value": 100000}
            ],
            "actions": [
                {"action_type": "banner", "severity": "warning", "banner_message": "High value opportunity detected"}
            ],
            "enabled": True,
            "priority": 5
        }
        
        response = self.session.post(f"{BASE_URL}/api/sales-ops/automation-rules", json=rule_payload)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "rule_id" in data, "Response should contain 'rule_id'"
        assert data["name"] == rule_payload["name"], "Name should match"
        assert data["trigger_category"] == "size", "Trigger category should match"
        assert data["trigger_type"] == "value_above", "Trigger type should match"
        assert data["enabled"] == True, "Rule should be enabled"
        
        self.created_rule_ids.append(data["rule_id"])
        print(f"✅ Created automation rule: {data['rule_id']}")
        
        return data["rule_id"]
    
    def test_create_rule_with_email_action(self):
        """Test creating rule with email action (MOCKED)"""
        rule_payload = {
            "name": "TEST_Email Notification Rule",
            "description": "Test email notification (MOCKED)",
            "trigger_category": "time",
            "trigger_type": "days_in_stage",
            "conditions": [
                {"field": "days_in_stage", "operator": "greater_than", "value": 7}
            ],
            "actions": [
                {"action_type": "banner", "severity": "critical", "banner_message": "Opportunity stale"},
                {"action_type": "email", "recipients": ["test@example.com"], "email_template": "stale_opportunity"}
            ],
            "enabled": True,
            "priority": 3
        }
        
        response = self.session.post(f"{BASE_URL}/api/sales-ops/automation-rules", json=rule_payload)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert len(data["actions"]) == 2, "Should have 2 actions"
        
        email_action = next((a for a in data["actions"] if a["action_type"] == "email"), None)
        assert email_action is not None, "Should have email action"
        assert "test@example.com" in email_action["recipients"], "Email recipient should be set"
        
        self.created_rule_ids.append(data["rule_id"])
        print(f"✅ Created rule with email action (MOCKED): {data['rule_id']}")
    
    def test_get_single_automation_rule(self):
        """Test GET /api/sales-ops/automation-rules/{rule_id} - Get single rule"""
        # First create a rule
        rule_id = self.test_create_automation_rule()
        
        # Then fetch it
        response = self.session.get(f"{BASE_URL}/api/sales-ops/automation-rules/{rule_id}")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data["rule_id"] == rule_id, "Rule ID should match"
        assert data["name"] == "TEST_High Value Alert", "Name should match"
        print(f"✅ GET single rule: {rule_id}")
    
    def test_update_automation_rule(self):
        """Test PUT /api/sales-ops/automation-rules/{rule_id} - Update rule"""
        # First create a rule
        rule_id = self.test_create_automation_rule()
        
        # Update it
        update_payload = {
            "name": "TEST_Updated High Value Alert",
            "description": "Updated description",
            "priority": 10
        }
        
        response = self.session.put(f"{BASE_URL}/api/sales-ops/automation-rules/{rule_id}", json=update_payload)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data["name"] == "TEST_Updated High Value Alert", "Name should be updated"
        assert data["priority"] == 10, "Priority should be updated"
        print(f"✅ Updated rule: {rule_id}")
    
    def test_toggle_automation_rule(self):
        """Test PATCH /api/sales-ops/automation-rules/{rule_id}/toggle - Toggle enabled/disabled"""
        # First create a rule (enabled by default)
        rule_id = self.test_create_automation_rule()
        
        # Toggle to disabled
        response = self.session.patch(f"{BASE_URL}/api/sales-ops/automation-rules/{rule_id}/toggle")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert data["rule_id"] == rule_id, "Rule ID should match"
        assert data["enabled"] == False, "Rule should be disabled after toggle"
        print(f"✅ Toggled rule to disabled: {rule_id}")
        
        # Toggle back to enabled
        response = self.session.patch(f"{BASE_URL}/api/sales-ops/automation-rules/{rule_id}/toggle")
        
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] == True, "Rule should be enabled after second toggle"
        print(f"✅ Toggled rule back to enabled: {rule_id}")
    
    def test_delete_automation_rule(self):
        """Test DELETE /api/sales-ops/automation-rules/{rule_id} - Delete rule"""
        # First create a rule
        create_payload = {
            "name": "TEST_Rule To Delete",
            "trigger_category": "margin",
            "trigger_type": "margin_below",
            "conditions": [{"field": "margin_percentage", "operator": "less_than", "value": 15}],
            "actions": [{"action_type": "banner", "severity": "warning"}],
            "enabled": True,
            "priority": 1
        }
        
        create_response = self.session.post(f"{BASE_URL}/api/sales-ops/automation-rules", json=create_payload)
        assert create_response.status_code == 200
        rule_id = create_response.json()["rule_id"]
        
        # Delete it
        response = self.session.delete(f"{BASE_URL}/api/sales-ops/automation-rules/{rule_id}")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "message" in data, "Should have success message"
        print(f"✅ Deleted rule: {rule_id}")
        
        # Verify it's gone
        get_response = self.session.get(f"{BASE_URL}/api/sales-ops/automation-rules/{rule_id}")
        assert get_response.status_code == 404, "Deleted rule should return 404"
        print(f"✅ Verified rule is deleted")
    
    def test_get_rule_not_found(self):
        """Test GET non-existent rule returns 404"""
        response = self.session.get(f"{BASE_URL}/api/sales-ops/automation-rules/nonexistent_rule_123")
        
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print(f"✅ Non-existent rule returns 404")
    
    def test_filter_rules_by_category(self):
        """Test filtering rules by category"""
        # Create rules with different categories
        time_rule = {
            "name": "TEST_Time Rule",
            "trigger_category": "time",
            "trigger_type": "days_in_stage",
            "conditions": [{"field": "days_in_stage", "operator": "greater_than", "value": 5}],
            "actions": [{"action_type": "banner", "severity": "warning"}],
            "enabled": True,
            "priority": 1
        }
        
        margin_rule = {
            "name": "TEST_Margin Rule",
            "trigger_category": "margin",
            "trigger_type": "margin_below",
            "conditions": [{"field": "margin_percentage", "operator": "less_than", "value": 20}],
            "actions": [{"action_type": "banner", "severity": "critical"}],
            "enabled": True,
            "priority": 1
        }
        
        # Create both rules
        time_response = self.session.post(f"{BASE_URL}/api/sales-ops/automation-rules", json=time_rule)
        margin_response = self.session.post(f"{BASE_URL}/api/sales-ops/automation-rules", json=margin_rule)
        
        assert time_response.status_code == 200
        assert margin_response.status_code == 200
        
        self.created_rule_ids.append(time_response.json()["rule_id"])
        self.created_rule_ids.append(margin_response.json()["rule_id"])
        
        # Filter by time category
        response = self.session.get(f"{BASE_URL}/api/sales-ops/automation-rules?category=time")
        assert response.status_code == 200
        
        data = response.json()
        for rule in data["items"]:
            if rule["name"].startswith("TEST_"):
                assert rule["trigger_category"] == "time", "Filtered rules should be time category"
        
        print(f"✅ Category filter working")


class TestAlertsAPI:
    """Test alerts endpoints"""
    
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
            data = login_response.json()
            token = data.get("access_token") or data.get("token")
            if token:
                self.session.headers.update({"Authorization": f"Bearer {token}"})
        
        yield
    
    def test_get_alerts_dashboard(self):
        """Test GET /api/sales-ops/alerts/dashboard - Get all active alerts"""
        response = self.session.get(f"{BASE_URL}/api/sales-ops/alerts/dashboard")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "items" in data, "Response should contain 'items'"
        assert "stats" in data, "Response should contain 'stats'"
        
        stats = data["stats"]
        assert "critical" in stats, "Stats should have critical count"
        assert "warning" in stats, "Stats should have warning count"
        assert "success" in stats, "Stats should have success count"
        assert "total" in stats, "Stats should have total count"
        
        print(f"✅ Alerts dashboard: {stats['total']} total alerts (critical: {stats['critical']}, warning: {stats['warning']}, success: {stats['success']})")
    
    def test_get_opportunity_alerts(self):
        """Test GET /api/sales-ops/opportunities/{id}/alerts - Get alerts for specific opportunity"""
        # First get an opportunity
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?limit=1")
        
        if opps_response.status_code == 200:
            opps_data = opps_response.json()
            if opps_data.get("items") and len(opps_data["items"]) > 0:
                opp_id = opps_data["items"][0]["opportunity_id"]
                
                response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp_id}/alerts")
                
                assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
                
                data = response.json()
                assert "items" in data, "Response should contain 'items'"
                assert "total" in data, "Response should contain 'total'"
                
                print(f"✅ Opportunity alerts: {data['total']} alerts for opportunity {opp_id}")
            else:
                pytest.skip("No opportunities available for testing")
        else:
            pytest.skip("Could not fetch opportunities")


class TestTriggerCategories:
    """Test all trigger categories are properly supported"""
    
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
            data = login_response.json()
            token = data.get("access_token") or data.get("token")
            if token:
                self.session.headers.update({"Authorization": f"Bearer {token}"})
        
        self.created_rule_ids = []
        yield
        
        # Cleanup
        for rule_id in self.created_rule_ids:
            try:
                self.session.delete(f"{BASE_URL}/api/sales-ops/automation-rules/{rule_id}")
            except:
                pass
    
    @pytest.mark.parametrize("category,trigger_type,field", [
        ("time", "days_in_stage", "days_in_stage"),
        ("margin", "margin_below", "margin_percentage"),
        ("size", "value_above", "opportunity_value"),
        ("status", "blockers_detected", "has_blockers"),
        ("activity", "new_attachments", "new_attachments_24h"),
        ("procurement", "inventory_low", "inventory_level"),
    ])
    def test_create_rule_for_each_category(self, category, trigger_type, field):
        """Test creating rules for each trigger category"""
        rule_payload = {
            "name": f"TEST_{category.upper()} Category Rule",
            "description": f"Test rule for {category} category",
            "trigger_category": category,
            "trigger_type": trigger_type,
            "conditions": [
                {"field": field, "operator": "greater_than", "value": 1}
            ],
            "actions": [
                {"action_type": "banner", "severity": "warning", "banner_message": f"Test {category} alert"}
            ],
            "enabled": True,
            "priority": 1
        }
        
        response = self.session.post(f"{BASE_URL}/api/sales-ops/automation-rules", json=rule_payload)
        
        assert response.status_code == 200, f"Failed to create {category} rule: {response.text}"
        
        data = response.json()
        assert data["trigger_category"] == category
        assert data["trigger_type"] == trigger_type
        
        self.created_rule_ids.append(data["rule_id"])
        print(f"✅ Created {category} category rule: {data['rule_id']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
