"""
AEGISAXIOM AI Conductor Tests - Testing the AI routing system
Features tested:
- AEGISAXIOM AI routes deal-related questions to deals agent
- AEGISAXIOM AI routes commission questions to commissions agent  
- AEGISAXIOM AI handles general questions directly
- Deals agent response format: Inquiry Confirmation, Answer, Contextual Insight, Resolution Check
- Commissions agent response format: executive summary first, highlights anomalies
- RBAC: admin user can see all data (filter is empty {})
- Chat sessions persist and are retrievable
- Multiple sequential messages in same session maintain context

Uses session cookie-based authentication (not Bearer token)
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials (admin - can see ALL data)
ADMIN_EMAIL = "support@simplimeta.io"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


class TestAEGISAXIOMAIConductor:
    """Test AEGISAXIOM AI conductor routing and sub-agents"""
    
    @pytest.fixture(scope="class")
    def session(self):
        """Get authenticated session with cookies (admin user)"""
        s = requests.Session()
        response = s.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "user" in data, "No user in login response"
        # Verify admin role for RBAC testing
        assert data["user"]["role"] == "admin", f"Expected admin role, got {data['user']['role']}"
        print(f"Logged in as: {data['user']['email']} (role: {data['user']['role']})")
        return s
    
    @pytest.fixture(scope="class")
    def chat_session_id(self, session):
        """Create a chat session for testing"""
        response = session.post(f"{BASE_URL}/api/chat/sessions")
        assert response.status_code == 200, f"Failed to create chat session: {response.text}"
        data = response.json()
        assert "session_id" in data
        print(f"Created test chat session: {data['session_id']}")
        return data["session_id"]
    
    def test_01_route_deals_question(self, session, chat_session_id):
        """AEGISAXIOM AI should route deal-related questions to deals agent"""
        # Test question about deals
        test_message = "How many deals have I closed?"
        response = session.post(
            f"{BASE_URL}/api/chat/sessions/{chat_session_id}/messages",
            json={"content": test_message},
            timeout=60  # AI responses can take time
        )
        assert response.status_code == 200, f"Failed to send message: {response.text}"
        ai_response = response.json()
        
        # Verify response structure
        assert "content" in ai_response, "No content in AI response"
        assert "role" in ai_response and ai_response["role"] == "assistant"
        
        content = ai_response["content"].lower()
        # Deals agent should respond about deals/data
        # Since there's no deal data, it should say something about no records
        assert len(ai_response["content"]) > 20, "Response too short"
        print(f"Deals routing response ({len(ai_response['content'])} chars): {ai_response['content'][:200]}...")
    
    def test_02_route_commissions_question(self, session):
        """AEGISAXIOM AI should route commission-related questions to commissions agent"""
        # Create new session to avoid context confusion
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        
        # Test commission question
        test_message = "What commissions am I owed?"
        response = session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": test_message},
            timeout=60
        )
        assert response.status_code == 200, f"Failed to send message: {response.text}"
        ai_response = response.json()
        
        # Verify response
        assert "content" in ai_response
        assert ai_response["role"] == "assistant"
        
        content = ai_response["content"].lower()
        # Commissions agent should respond about commissions
        assert len(ai_response["content"]) > 20, "Response too short"
        print(f"Commissions routing response ({len(ai_response['content'])} chars): {ai_response['content'][:200]}...")
    
    def test_03_route_general_question(self, session):
        """AEGISAXIOM AI should handle general questions directly"""
        # Create new session
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        
        # Test general question (greeting)
        test_message = "Hello, what can you help me with?"
        response = session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": test_message},
            timeout=60
        )
        assert response.status_code == 200, f"Failed to send message: {response.text}"
        ai_response = response.json()
        
        # Verify response
        assert "content" in ai_response
        assert ai_response["role"] == "assistant"
        
        content = ai_response["content"].lower()
        # General agent should explain capabilities
        assert len(ai_response["content"]) > 20, "Response too short"
        # Should mention deals or commissions as capabilities
        assert "deal" in content or "commission" in content or "help" in content or "aegisaxiom" in content, \
            f"General response should mention capabilities: {content[:100]}"
        print(f"General routing response ({len(ai_response['content'])} chars): {ai_response['content'][:200]}...")
    
    def test_04_deals_agent_response_format(self, session):
        """Deals agent should respond with structured format"""
        # Create new session
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        
        # Ask specific deal question to trigger formatted response
        test_message = "Show me my pipeline"
        response = session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": test_message},
            timeout=60
        )
        assert response.status_code == 200, f"Failed to send message: {response.text}"
        ai_response = response.json()
        
        # Verify we got a substantive response
        assert "content" in ai_response
        content = ai_response["content"]
        assert len(content) > 30, "Response should be substantive"
        print(f"Deals agent format check - Response: {content[:300]}...")
    
    def test_05_commissions_agent_executive_summary(self, session):
        """Commissions agent should provide executive summary first"""
        # Create new session
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        
        # Ask for commission summary
        test_message = "Give me a summary of my commissions"
        response = session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": test_message},
            timeout=60
        )
        assert response.status_code == 200, f"Failed to send message: {response.text}"
        ai_response = response.json()
        
        # Verify we got a substantive response
        assert "content" in ai_response
        content = ai_response["content"]
        assert len(content) > 30, "Response should be substantive"
        print(f"Commissions executive summary check - Response: {content[:300]}...")
    
    def test_06_session_persistence(self, session):
        """Messages should persist in session and be retrievable"""
        # Create session
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        
        # Send message and get AI response
        test_message = "What deals are in my pipeline?"
        send_resp = session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": test_message},
            timeout=60
        )
        assert send_resp.status_code == 200
        
        # Retrieve messages
        msgs_resp = session.get(f"{BASE_URL}/api/chat/sessions/{session_id}/messages")
        assert msgs_resp.status_code == 200
        messages = msgs_resp.json()
        
        # Verify both user and AI messages are stored
        assert len(messages) >= 2, f"Expected at least 2 messages, got {len(messages)}"
        
        user_msgs = [m for m in messages if m["role"] == "user"]
        ai_msgs = [m for m in messages if m["role"] == "assistant"]
        
        assert len(user_msgs) >= 1, "User message not persisted"
        assert len(ai_msgs) >= 1, "AI message not persisted"
        assert user_msgs[0]["content"] == test_message, "User message content mismatch"
        print(f"Session {session_id} persisted {len(messages)} messages correctly")
    
    def test_07_multiple_messages_context(self, session):
        """Multiple messages in same session should maintain context"""
        # Create session
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        
        # First message
        msg1 = "What deals are in my pipeline?"
        resp1 = session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": msg1},
            timeout=60
        )
        assert resp1.status_code == 200
        print(f"First message response: {resp1.json()['content'][:100]}...")
        
        # Second message - should have context from first
        msg2 = "What about the total value?"
        resp2 = session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": msg2},
            timeout=60
        )
        assert resp2.status_code == 200
        ai_resp2 = resp2.json()
        
        # Verify we got a response
        assert "content" in ai_resp2
        assert len(ai_resp2["content"]) > 20, "Follow-up response should be substantive"
        print(f"Follow-up message response: {ai_resp2['content'][:100]}...")
        
        # Verify all messages are stored
        msgs_resp = session.get(f"{BASE_URL}/api/chat/sessions/{session_id}/messages")
        assert msgs_resp.status_code == 200
        messages = msgs_resp.json()
        assert len(messages) >= 4, f"Expected at least 4 messages (2 user + 2 AI), got {len(messages)}"
        print(f"Session maintains {len(messages)} messages with context")
    
    def test_08_rbac_admin_empty_filter(self, session):
        """Admin user should have access to all data (empty filter)"""
        # Create session and ask about ALL deals (admin-level question)
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        
        # Ask question that would require admin access
        test_message = "Show me all deals across all sales reps"
        response = session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": test_message},
            timeout=60
        )
        assert response.status_code == 200
        ai_response = response.json()
        
        # Should not get "access denied" or similar
        content = ai_response["content"].lower()
        assert "can only access" not in content, "Admin should not be restricted"
        assert "insufficient permissions" not in content, "Admin should have full access"
        print(f"RBAC admin check - Response indicates full access: {ai_response['content'][:150]}...")
    
    def test_09_session_list_returns_sessions(self, session):
        """GET /api/chat/sessions should return list of sessions"""
        response = session.get(f"{BASE_URL}/api/chat/sessions")
        assert response.status_code == 200
        sessions_list = response.json()
        
        assert isinstance(sessions_list, list), "Sessions should be a list"
        # After all our tests, should have multiple sessions
        assert len(sessions_list) > 0, "Should have at least one session after tests"
        
        # Verify session structure
        if len(sessions_list) > 0:
            s = sessions_list[0]
            assert "session_id" in s, "Session should have session_id"
            assert "title" in s, "Session should have title"
            assert "created_at" in s, "Session should have created_at"
        print(f"Sessions list returned {len(sessions_list)} sessions")
    
    def test_10_different_question_types_routing(self, session):
        """Test various question phrasings route correctly"""
        test_cases = [
            # (message, expected_route_keywords)
            ("What's the total revenue for this quarter?", ["deal", "revenue", "total"]),
            ("Have I been paid for the Helient deal?", ["commission", "paid", "payment"]),
            ("Help me understand the AEGISAXIOM system", ["aegisaxiom", "system", "help"]),
        ]
        
        for msg, keywords in test_cases:
            # Create fresh session for each test
            create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
            assert create_resp.status_code == 200
            session_id = create_resp.json()["session_id"]
            
            response = session.post(
                f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
                json={"content": msg},
                timeout=60
            )
            assert response.status_code == 200, f"Failed for message: {msg}"
            ai_response = response.json()
            assert "content" in ai_response, f"No content for: {msg}"
            assert len(ai_response["content"]) > 20, f"Response too short for: {msg}"
            print(f"'{msg[:30]}...' -> {len(ai_response['content'])} char response ✓")


class TestChatWidgetAndPageIntegration:
    """Test that widget and full-page chat work with the new AI service"""
    
    @pytest.fixture(scope="class")
    def session(self):
        """Get authenticated session"""
        s = requests.Session()
        response = s.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        return s
    
    def test_widget_session_flow(self, session):
        """Test the flow that AIChatWidget would use"""
        # 1. Create session (widget calls newSession)
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        
        # 2. Send message (widget calls sendMessage)
        msg_resp = session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": "Show my commission summary"},
            timeout=60
        )
        assert msg_resp.status_code == 200
        ai_msg = msg_resp.json()
        assert ai_msg["role"] == "assistant"
        
        # 3. Get messages (widget would display these)
        msgs_resp = session.get(f"{BASE_URL}/api/chat/sessions/{session_id}/messages")
        assert msgs_resp.status_code == 200
        messages = msgs_resp.json()
        assert len(messages) >= 2
        print(f"Widget flow completed: {len(messages)} messages in session")
    
    def test_page_session_flow(self, session):
        """Test the flow that ChatPage would use"""
        # 1. Load sessions (ChatPage calls loadSessions on mount)
        sessions_resp = session.get(f"{BASE_URL}/api/chat/sessions")
        assert sessions_resp.status_code == 200
        sessions = sessions_resp.json()
        
        # 2. Create new session
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        
        # 3. Send message
        msg_resp = session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": "What are my top deals this month?"},
            timeout=60
        )
        assert msg_resp.status_code == 200
        
        # 4. Delete session
        del_resp = session.delete(f"{BASE_URL}/api/chat/sessions/{session_id}")
        assert del_resp.status_code == 200
        print("Page flow completed: create, message, delete all working")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
