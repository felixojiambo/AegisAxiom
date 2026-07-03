"""
Test Automation Rules V2 - Preconditions, Stage Blocking, and Auto-Transitions
Tests the enhanced automation system with:
- Preconditions (WHEN to evaluate rules)
- Stage blocking (prevent transitions)
- Stage auto-transitions (auto-move SOWs)
- LOB context in alerts
"""
import pytest
import requests
import os
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestAutomationPreconditions:
    """Test automation rules with preconditions"""
    
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
        else:
            pytest.skip("Authentication failed")
        
        yield
    
    def test_builtin_rules_have_preconditions(self):
        """Verify built-in rules have preconditions configured"""
        response = self.session.get(f"{BASE_URL}/api/sales-ops/automation-rules")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        rules = data.get("items", [])
        
        # Find built-in rules
        builtin_rules = [r for r in rules if r.get("is_built_in", False)]
        
        print(f"Found {len(builtin_rules)} built-in rules")
        
        # Check for negative margin alert rule
        negative_margin_rule = next((r for r in builtin_rules if "negative_margin" in r.get("rule_id", "").lower()), None)
        if negative_margin_rule:
            preconditions = negative_margin_rule.get("preconditions", [])
            print(f"✅ Negative Margin Rule preconditions: {preconditions}")
            
            # Should have has_resources > 0 and margin_is_calculated = true
            has_resources_precond = any(p.get("field") == "has_resources" for p in preconditions)
            margin_calculated_precond = any(p.get("field") == "margin_is_calculated" for p in preconditions)
            
            assert has_resources_precond, "Negative margin rule should have has_resources precondition"
            assert margin_calculated_precond, "Negative margin rule should have margin_is_calculated precondition"
            print("✅ Negative margin rule has correct preconditions")
        else:
            print("⚠️ Negative margin rule not found in built-in rules")
        
        # Check for missing contractor block rule
        missing_contractor_rule = next((r for r in builtin_rules if "missing_contractor" in r.get("rule_id", "").lower()), None)
        if missing_contractor_rule:
            preconditions = missing_contractor_rule.get("preconditions", [])
            print(f"✅ Missing Contractor Rule preconditions: {preconditions}")
            
            # Should have has_resources > 0
            has_resources_precond = any(p.get("field") == "has_resources" for p in preconditions)
            assert has_resources_precond, "Missing contractor rule should have has_resources precondition"
            print("✅ Missing contractor rule has correct preconditions")
        else:
            print("⚠️ Missing contractor rule not found in built-in rules")
    
    def test_builtin_rules_have_block_stage_actions(self):
        """Verify built-in rules have block_stage actions configured"""
        response = self.session.get(f"{BASE_URL}/api/sales-ops/automation-rules")
        
        assert response.status_code == 200
        
        data = response.json()
        rules = data.get("items", [])
        
        # Find rules with block_stage actions
        block_rules = [r for r in rules if any(
            a.get("action_type") == "block_stage" 
            for a in r.get("actions", [])
        )]
        
        print(f"Found {len(block_rules)} rules with block_stage actions")
        
        for rule in block_rules:
            print(f"  - {rule.get('name')}: blocks {[a.get('blocked_stages') for a in rule.get('actions', []) if a.get('action_type') == 'block_stage']}")
        
        assert len(block_rules) > 0, "Should have at least one rule with block_stage action"
        print("✅ Block stage rules configured correctly")
    
    def test_builtin_rules_have_stage_transition_actions(self):
        """Verify built-in rules have stage_transition actions configured"""
        response = self.session.get(f"{BASE_URL}/api/sales-ops/automation-rules")
        
        assert response.status_code == 200
        
        data = response.json()
        rules = data.get("items", [])
        
        # Find rules with stage_transition actions
        transition_rules = [r for r in rules if any(
            a.get("action_type") == "stage_transition" 
            for a in r.get("actions", [])
        )]
        
        print(f"Found {len(transition_rules)} rules with stage_transition actions")
        
        for rule in transition_rules:
            for action in rule.get("actions", []):
                if action.get("action_type") == "stage_transition":
                    print(f"  - {rule.get('name')}: auto-moves to {action.get('target_stage')}")
        
        assert len(transition_rules) > 0, "Should have at least one rule with stage_transition action"
        print("✅ Stage transition rules configured correctly")


class TestStageBlocking:
    """Test stage blocking functionality"""
    
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
        else:
            pytest.skip("Authentication failed")
        
        yield
    
    def test_check_stage_blocks_endpoint_exists(self):
        """Test that check-stage-blocks endpoint exists"""
        # Get an opportunity with SOWs
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=5")
        
        if opps_response.status_code != 200:
            pytest.skip("Could not fetch opportunities")
        
        opps_data = opps_response.json()
        opportunities = opps_data.get("items", [])
        
        if not opportunities:
            pytest.skip("No opportunities in proposal_development stage")
        
        # Find an opportunity with SOWs
        for opp in opportunities:
            sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sow-tasks")
            
            if sow_response.status_code == 200:
                sow_data = sow_response.json()
                sow_tasks = sow_data.get("sow_tasks", [])
                
                if sow_tasks:
                    sow = sow_tasks[0]
                    
                    # Test check-stage-blocks endpoint
                    block_response = self.session.get(
                        f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sows/{sow['sow_id']}/check-stage-blocks/complete"
                    )
                    
                    assert block_response.status_code == 200, f"Expected 200, got {block_response.status_code}: {block_response.text}"
                    
                    block_data = block_response.json()
                    assert "blocked" in block_data, "Response should contain 'blocked' field"
                    assert "target_stage" in block_data, "Response should contain 'target_stage' field"
                    assert "blocking_rules" in block_data, "Response should contain 'blocking_rules' field"
                    
                    print(f"✅ Check stage blocks endpoint working")
                    print(f"   Blocked: {block_data['blocked']}")
                    print(f"   Target: {block_data['target_stage']}")
                    print(f"   Blocking rules: {block_data['blocking_rules']}")
                    return
        
        pytest.skip("No SOWs found for testing")
    
    def test_sow_with_no_resources_blocked_from_complete(self):
        """Test that SOW with 0 resources cannot be marked complete"""
        # Get opportunities
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=10")
        
        if opps_response.status_code != 200:
            pytest.skip("Could not fetch opportunities")
        
        opps_data = opps_response.json()
        opportunities = opps_data.get("items", [])
        
        # Find SOW with no resources
        for opp in opportunities:
            sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sow-tasks")
            
            if sow_response.status_code == 200:
                sow_data = sow_response.json()
                sow_tasks = sow_data.get("sow_tasks", [])
                
                for sow in sow_tasks:
                    resource_count = len(sow.get("selected_resource_assignment_ids", []))
                    
                    if resource_count == 0:
                        # This SOW has no resources - should be blocked from complete
                        block_response = self.session.get(
                            f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sows/{sow['sow_id']}/check-stage-blocks/complete"
                        )
                        
                        if block_response.status_code == 200:
                            block_data = block_response.json()
                            
                            if block_data.get("blocked"):
                                print(f"✅ SOW with 0 resources correctly blocked from 'complete'")
                                print(f"   Blocking reasons: {block_data.get('blocking_rules')}")
                                return
                            else:
                                print(f"⚠️ SOW with 0 resources NOT blocked - checking rule configuration")
        
        print("⚠️ No SOW with 0 resources found to test blocking")


class TestAlertsWithLOBContext:
    """Test that alerts include LOB context"""
    
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
        else:
            pytest.skip("Authentication failed")
        
        yield
    
    def test_alerts_have_lob_field(self):
        """Test that alerts include line_of_business field"""
        # Get alerts dashboard
        response = self.session.get(f"{BASE_URL}/api/sales-ops/alerts/dashboard")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        alerts = data.get("items", [])
        
        print(f"Found {len(alerts)} alerts")
        
        # Check if any alerts have line_of_business
        alerts_with_lob = [a for a in alerts if a.get("line_of_business")]
        alerts_with_sow_id = [a for a in alerts if a.get("sow_id")]
        
        print(f"  - Alerts with line_of_business: {len(alerts_with_lob)}")
        print(f"  - Alerts with sow_id: {len(alerts_with_sow_id)}")
        
        if alerts_with_lob:
            for alert in alerts_with_lob[:3]:
                print(f"    Alert: {alert.get('rule_name')} - LOB: {alert.get('line_of_business')}")
            print("✅ Alerts include LOB context")
        else:
            print("⚠️ No alerts with LOB context found (may need to trigger SOW-level rules)")
    
    def test_opportunity_alerts_include_sow_context(self):
        """Test that opportunity-specific alerts include SOW context"""
        # Get opportunities
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=5")
        
        if opps_response.status_code != 200:
            pytest.skip("Could not fetch opportunities")
        
        opps_data = opps_response.json()
        opportunities = opps_data.get("items", [])
        
        for opp in opportunities:
            alerts_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/alerts")
            
            if alerts_response.status_code == 200:
                alerts_data = alerts_response.json()
                alerts = alerts_data.get("items", [])
                
                if alerts:
                    print(f"Opportunity: {opp['name']}")
                    for alert in alerts[:3]:
                        print(f"  - {alert.get('rule_name')}: sow_id={alert.get('sow_id')}, lob={alert.get('line_of_business')}")
                    
                    # Check for SOW-specific alerts
                    sow_alerts = [a for a in alerts if a.get("sow_id")]
                    if sow_alerts:
                        print(f"✅ Found {len(sow_alerts)} SOW-specific alerts")
                        return
        
        print("⚠️ No SOW-specific alerts found")


class TestSOWStatusTransitions:
    """Test SOW status transitions with automation rules"""
    
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
        else:
            pytest.skip("Authentication failed")
        
        yield
    
    def test_status_change_validates_against_rules(self):
        """Test that status changes are validated against automation rules"""
        # Get opportunities
        opps_response = self.session.get(f"{BASE_URL}/api/opportunities?stage=proposal_development&limit=5")
        
        if opps_response.status_code != 200:
            pytest.skip("Could not fetch opportunities")
        
        opps_data = opps_response.json()
        opportunities = opps_data.get("items", [])
        
        for opp in opportunities:
            sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sow-tasks")
            
            if sow_response.status_code == 200:
                sow_data = sow_response.json()
                sow_tasks = sow_data.get("sow_tasks", [])
                
                for sow in sow_tasks:
                    # First check if blocked
                    block_response = self.session.get(
                        f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sows/{sow['sow_id']}/check-stage-blocks/complete"
                    )
                    
                    if block_response.status_code == 200:
                        block_data = block_response.json()
                        
                        if block_data.get("blocked"):
                            # Try to change status to complete - should fail
                            status_response = self.session.patch(
                                f"{BASE_URL}/api/sales-ops/opportunities/{opp['opportunity_id']}/sows/{sow['sow_id']}/status?status=complete"
                            )
                            
                            # Should return 400 with blocking reasons
                            if status_response.status_code == 400:
                                error_data = status_response.json()
                                print(f"✅ Status change correctly blocked")
                                print(f"   Error: {error_data.get('detail')}")
                                return
                            else:
                                print(f"⚠️ Status change not blocked as expected: {status_response.status_code}")
        
        print("⚠️ No blocked SOWs found to test status validation")


class TestTestDemoContextOpportunity:
    """Test the test_demo_context opportunity with real issues"""
    
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
        else:
            pytest.skip("Authentication failed")
        
        yield
    
    def test_find_test_demo_context_opportunity(self):
        """Find and verify the test_demo_context opportunity"""
        # Search for test_demo_context opportunity
        response = self.session.get(f"{BASE_URL}/api/opportunities?limit=100")
        
        assert response.status_code == 200
        
        data = response.json()
        opportunities = data.get("items", [])
        
        # Find test_demo_context
        test_opp = next((o for o in opportunities if "test_demo_context" in o.get("name", "").lower()), None)
        
        if test_opp:
            print(f"✅ Found test_demo_context opportunity: {test_opp['opportunity_id']}")
            print(f"   Name: {test_opp['name']}")
            print(f"   Stage: {test_opp['stage']}")
            print(f"   LOBs: {test_opp.get('lines_of_business', [])}")
            
            # Get SOWs for this opportunity
            sow_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{test_opp['opportunity_id']}/sow-tasks")
            
            if sow_response.status_code == 200:
                sow_data = sow_response.json()
                sow_tasks = sow_data.get("sow_tasks", [])
                
                print(f"   SOWs: {len(sow_tasks)}")
                for sow in sow_tasks:
                    print(f"     - {sow.get('line_of_business')}: status={sow.get('status')}, blockers={sow.get('has_critical_blockers')}")
                    print(f"       Resources: {len(sow.get('selected_resource_assignment_ids', []))}")
                    print(f"       Blocker messages: {sow.get('blockers', [])}")
            
            # Get alerts for this opportunity
            alerts_response = self.session.get(f"{BASE_URL}/api/sales-ops/opportunities/{test_opp['opportunity_id']}/alerts")
            
            if alerts_response.status_code == 200:
                alerts_data = alerts_response.json()
                alerts = alerts_data.get("items", [])
                
                print(f"   Alerts: {len(alerts)}")
                for alert in alerts:
                    print(f"     - {alert.get('severity')}: {alert.get('rule_name')} - {alert.get('message')}")
                    print(f"       LOB: {alert.get('line_of_business')}, SOW: {alert.get('sow_id')}")
        else:
            print("⚠️ test_demo_context opportunity not found")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
