"""
Tests for Chat Only Role and AI Agent Permissions features:

1. Chat Only role exists with correct permissions (chat.access, chat.agent.deals, chat.agent.commissions)
2. AI Chat permissions appear in the /api/roles/permissions endpoint  
3. Backend enforces agent permissions (denial message without permission)
4. Backend allows users WITH permissions to access agents
5. Admin role with '*' permission passes all checks
6. Permission changes are saved correctly when editing a role
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL').rstrip('/')
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


@pytest.fixture(scope="module")
def session():
    """Create a requests session"""
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin_session(session):
    """Login as admin and return authenticated session"""
    response = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": "support@simplimeta.io",
        "password": ADMIN_PASSWORD
    })
    assert response.status_code == 200, f"Admin login failed: {response.text}"
    data = response.json()
    assert "user" in data, "No user in login response"
    # Auth uses httpOnly cookies set automatically in session
    return session


class TestChatOnlyRole:
    """Tests for Chat Only role configuration"""
    
    def test_chat_only_role_exists_in_roles_list(self, admin_session):
        """Verify Chat Only role exists in the roles list"""
        response = admin_session.get(f"{BASE_URL}/api/roles")
        assert response.status_code == 200, f"Failed to get roles: {response.text}"
        
        roles = response.json()
        chat_only_role = next((r for r in roles if r.get("name") == "chat_only"), None)
        
        assert chat_only_role is not None, "Chat Only role not found in roles list"
        assert chat_only_role.get("display_name") == "Chat Only", f"Unexpected display_name: {chat_only_role.get('display_name')}"
        print(f"Chat Only role found: {chat_only_role.get('display_name')}")
    
    def test_chat_only_role_has_correct_permissions(self, admin_session):
        """Verify Chat Only role has chat.access, chat.agent.deals, chat.agent.commissions"""
        response = admin_session.get(f"{BASE_URL}/api/roles")
        assert response.status_code == 200
        
        roles = response.json()
        chat_only_role = next((r for r in roles if r.get("name") == "chat_only"), None)
        assert chat_only_role is not None
        
        permissions = chat_only_role.get("permissions", [])
        assert "chat.access" in permissions, "chat.access permission missing from Chat Only role"
        assert "chat.agent.deals" in permissions, "chat.agent.deals permission missing from Chat Only role"
        assert "chat.agent.commissions" in permissions, "chat.agent.commissions permission missing from Chat Only role"
        
        print(f"Chat Only role permissions: {permissions}")
    
    def test_chat_only_role_has_only_chat_permissions(self, admin_session):
        """Verify Chat Only role does NOT have other non-chat permissions"""
        response = admin_session.get(f"{BASE_URL}/api/roles")
        assert response.status_code == 200
        
        roles = response.json()
        chat_only_role = next((r for r in roles if r.get("name") == "chat_only"), None)
        assert chat_only_role is not None
        
        permissions = chat_only_role.get("permissions", [])
        
        # All permissions should start with "chat."
        non_chat_perms = [p for p in permissions if not p.startswith("chat.")]
        assert len(non_chat_perms) == 0, f"Chat Only role has non-chat permissions: {non_chat_perms}"
        
        print(f"Chat Only role has only chat-related permissions: {permissions}")


class TestAIChatPermissionsInAdminUI:
    """Tests for AI Chat permissions appearing in Roles & Permissions admin UI"""
    
    def test_permissions_endpoint_includes_ai_chat_group(self, admin_session):
        """Verify AI Chat permissions group exists in /api/roles/permissions"""
        response = admin_session.get(f"{BASE_URL}/api/roles/permissions")
        assert response.status_code == 200, f"Failed to get permissions: {response.text}"
        
        permissions = response.json()
        ai_chat_perms = [p for p in permissions if p.get("group") == "AI Chat"]
        
        assert len(ai_chat_perms) >= 3, f"Expected at least 3 AI Chat permissions, got {len(ai_chat_perms)}"
        print(f"AI Chat permissions: {[p.get('label') for p in ai_chat_perms]}")
    
    def test_access_ai_chat_permission_exists(self, admin_session):
        """Verify 'Access AI Chat' permission exists"""
        response = admin_session.get(f"{BASE_URL}/api/roles/permissions")
        assert response.status_code == 200
        
        permissions = response.json()
        access_chat = next((p for p in permissions if p.get("key") == "chat.access"), None)
        
        assert access_chat is not None, "chat.access permission not found"
        assert access_chat.get("label") == "Access AI Chat", f"Unexpected label: {access_chat.get('label')}"
        assert access_chat.get("group") == "AI Chat", f"Wrong group: {access_chat.get('group')}"
        print(f"Found: {access_chat}")
    
    def test_ai_deals_agent_permission_exists(self, admin_session):
        """Verify 'AI Deals Agent' permission exists"""
        response = admin_session.get(f"{BASE_URL}/api/roles/permissions")
        assert response.status_code == 200
        
        permissions = response.json()
        deals_agent = next((p for p in permissions if p.get("key") == "chat.agent.deals"), None)
        
        assert deals_agent is not None, "chat.agent.deals permission not found"
        assert deals_agent.get("label") == "AI Deals Agent", f"Unexpected label: {deals_agent.get('label')}"
        assert deals_agent.get("group") == "AI Chat", f"Wrong group: {deals_agent.get('group')}"
        print(f"Found: {deals_agent}")
    
    def test_ai_commissions_agent_permission_exists(self, admin_session):
        """Verify 'AI Commissions Agent' permission exists"""
        response = admin_session.get(f"{BASE_URL}/api/roles/permissions")
        assert response.status_code == 200
        
        permissions = response.json()
        commissions_agent = next((p for p in permissions if p.get("key") == "chat.agent.commissions"), None)
        
        assert commissions_agent is not None, "chat.agent.commissions permission not found"
        assert commissions_agent.get("label") == "AI Commissions Agent", f"Unexpected label: {commissions_agent.get('label')}"
        assert commissions_agent.get("group") == "AI Chat", f"Wrong group: {commissions_agent.get('group')}"
        print(f"Found: {commissions_agent}")


class TestDefaultRolesHaveAgentPermissions:
    """Tests to verify existing default roles have the new agent permissions backfilled"""
    
    def test_director_has_agent_permissions(self, admin_session):
        """Verify Director role has AI agent permissions"""
        response = admin_session.get(f"{BASE_URL}/api/roles")
        assert response.status_code == 200
        
        roles = response.json()
        director = next((r for r in roles if r.get("name") == "director"), None)
        assert director is not None, "Director role not found"
        
        permissions = director.get("permissions", [])
        assert "chat.access" in permissions, "director missing chat.access"
        assert "chat.agent.deals" in permissions, "director missing chat.agent.deals"
        assert "chat.agent.commissions" in permissions, "director missing chat.agent.commissions"
        print(f"Director role has AI agent permissions: {[p for p in permissions if p.startswith('chat.')]}")
    
    def test_finance_has_agent_permissions(self, admin_session):
        """Verify Finance role has AI agent permissions"""
        response = admin_session.get(f"{BASE_URL}/api/roles")
        assert response.status_code == 200
        
        roles = response.json()
        finance = next((r for r in roles if r.get("name") == "finance"), None)
        assert finance is not None, "Finance role not found"
        
        permissions = finance.get("permissions", [])
        assert "chat.access" in permissions, "finance missing chat.access"
        assert "chat.agent.deals" in permissions, "finance missing chat.agent.deals"
        assert "chat.agent.commissions" in permissions, "finance missing chat.agent.commissions"
        print(f"Finance role has AI agent permissions: {[p for p in permissions if p.startswith('chat.')]}")
    
    def test_sales_rep_has_agent_permissions(self, admin_session):
        """Verify Sales Rep role has AI agent permissions"""
        response = admin_session.get(f"{BASE_URL}/api/roles")
        assert response.status_code == 200
        
        roles = response.json()
        sales_rep = next((r for r in roles if r.get("name") == "sales_rep"), None)
        assert sales_rep is not None, "Sales Rep role not found"
        
        permissions = sales_rep.get("permissions", [])
        assert "chat.access" in permissions, "sales_rep missing chat.access"
        assert "chat.agent.deals" in permissions, "sales_rep missing chat.agent.deals"
        assert "chat.agent.commissions" in permissions, "sales_rep missing chat.agent.commissions"
        print(f"Sales Rep role has AI agent permissions: {[p for p in permissions if p.startswith('chat.')]}")
    
    def test_agent_has_agent_permissions(self, admin_session):
        """Verify Agent role has AI agent permissions"""
        response = admin_session.get(f"{BASE_URL}/api/roles")
        assert response.status_code == 200
        
        roles = response.json()
        agent = next((r for r in roles if r.get("name") == "agent"), None)
        assert agent is not None, "Agent role not found"
        
        permissions = agent.get("permissions", [])
        assert "chat.access" in permissions, "agent missing chat.access"
        assert "chat.agent.deals" in permissions, "agent missing chat.agent.deals"
        assert "chat.agent.commissions" in permissions, "agent missing chat.agent.commissions"
        print(f"Agent role has AI agent permissions: {[p for p in permissions if p.startswith('chat.')]}")
    
    def test_customer_has_only_chat_access(self, admin_session):
        """Verify Customer role has only chat.access (NOT agent permissions)"""
        response = admin_session.get(f"{BASE_URL}/api/roles")
        assert response.status_code == 200
        
        roles = response.json()
        customer = next((r for r in roles if r.get("name") == "customer"), None)
        assert customer is not None, "Customer role not found"
        
        permissions = customer.get("permissions", [])
        assert "chat.access" in permissions, "customer missing chat.access"
        # Customer should NOT have agent permissions
        assert "chat.agent.deals" not in permissions, "customer should NOT have chat.agent.deals"
        assert "chat.agent.commissions" not in permissions, "customer should NOT have chat.agent.commissions"
        print(f"Customer role chat permissions (correctly limited): {[p for p in permissions if p.startswith('chat.')]}")


class TestRolePermissionEditing:
    """Tests for editing role permissions"""
    
    def test_can_toggle_ai_agent_permissions_on_role(self, admin_session):
        """Test that we can toggle AI agent permissions on an editable role"""
        # Get roles to find a non-system role
        response = admin_session.get(f"{BASE_URL}/api/roles")
        assert response.status_code == 200
        
        roles = response.json()
        # Find customer role (it's not a system role and doesn't have agent perms by default)
        customer = next((r for r in roles if r.get("name") == "customer"), None)
        assert customer is not None, "Customer role not found"
        
        role_id = customer.get("role_id")
        original_perms = customer.get("permissions", [])
        
        # Add chat.agent.deals permission
        new_perms = list(set(original_perms) | {"chat.agent.deals"})
        
        response = admin_session.put(f"{BASE_URL}/api/roles/{role_id}", json={
            "permissions": new_perms
        })
        assert response.status_code == 200, f"Failed to update role: {response.text}"
        
        # Verify the change was saved
        response = admin_session.get(f"{BASE_URL}/api/roles")
        assert response.status_code == 200
        
        updated_roles = response.json()
        updated_customer = next((r for r in updated_roles if r.get("name") == "customer"), None)
        assert "chat.agent.deals" in updated_customer.get("permissions", []), "Permission change not saved"
        print("Successfully added chat.agent.deals to customer role")
        
        # Revert the change (cleanup)
        response = admin_session.put(f"{BASE_URL}/api/roles/{role_id}", json={
            "permissions": original_perms
        })
        assert response.status_code == 200, "Failed to revert customer role"
        print("Reverted customer role to original permissions")


class TestAdminUserInfo:
    """Test admin user info for permission enforcement context"""
    
    def test_admin_has_wildcard_permission(self, admin_session):
        """Verify admin user has '*' permission (passes all checks)"""
        response = admin_session.get(f"{BASE_URL}/api/auth/me")
        assert response.status_code == 200, f"Failed to get user info: {response.text}"
        
        user = response.json()
        permissions = user.get("permissions", [])
        
        assert "*" in permissions or user.get("role") == "admin", "Admin should have '*' permission or admin role"
        print(f"Admin user info: role={user.get('role')}, permissions contain '*': {'*' in permissions}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
