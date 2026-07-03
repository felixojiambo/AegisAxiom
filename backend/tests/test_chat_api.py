"""
Chat API Tests - Testing /api/chat/* endpoints
Features: sessions CRUD, messages, AI responses, widget-to-page sync via shared context
Uses session cookie-based authentication (not Bearer token)
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
ADMIN_EMAIL = "support@simplimeta.io"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


class TestChatAPI:
    """Chat API endpoint tests"""
    
    @pytest.fixture(scope="class")
    def session(self):
        """Get authenticated session with cookies"""
        s = requests.Session()
        response = s.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        data = response.json()
        assert "user" in data, "No user in login response"
        # Session cookies are automatically stored in the session
        print(f"Logged in as: {data['user']['email']} (role: {data['user']['role']})")
        return s
    
    def test_01_get_sessions_empty_or_list(self, session):
        """GET /api/chat/sessions - should return list (may be empty or have sessions)"""
        response = session.get(f"{BASE_URL}/api/chat/sessions")
        assert response.status_code == 200, f"Failed to get sessions: {response.text}"
        data = response.json()
        assert isinstance(data, list), "Sessions should be a list"
        print(f"Got {len(data)} sessions")
    
    def test_02_create_session(self, session):
        """POST /api/chat/sessions - creates new chat session"""
        response = session.post(f"{BASE_URL}/api/chat/sessions")
        assert response.status_code == 200, f"Failed to create session: {response.text}"
        data = response.json()
        assert "session_id" in data, "session_id not in response"
        assert "user_id" in data, "user_id not in response"
        assert "title" in data, "title not in response"
        assert data["title"] == "New Chat", f"Expected 'New Chat', got '{data['title']}'"
        print(f"Created session: {data['session_id']}")
    
    def test_03_get_sessions_after_create(self, session):
        """Verify session appears in list after creation"""
        # Create a session first
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        created_session = create_resp.json()
        session_id = created_session["session_id"]
        
        # Get sessions list
        response = session.get(f"{BASE_URL}/api/chat/sessions")
        assert response.status_code == 200
        sessions = response.json()
        
        # Verify created session is in list
        session_ids = [s["session_id"] for s in sessions]
        assert session_id in session_ids, "Created session not found in sessions list"
        print(f"Verified session {session_id} in list")
    
    def test_04_get_messages_empty(self, session):
        """GET /api/chat/sessions/{id}/messages - empty for new session"""
        # Create session first
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        
        # Get messages
        response = session.get(f"{BASE_URL}/api/chat/sessions/{session_id}/messages")
        assert response.status_code == 200, f"Failed to get messages: {response.text}"
        messages = response.json()
        assert isinstance(messages, list), "Messages should be a list"
        assert len(messages) == 0, "New session should have empty messages"
        print(f"Session {session_id} has 0 messages (correct for new session)")
    
    def test_05_send_message_and_get_ai_response(self, session):
        """POST /api/chat/sessions/{id}/messages - sends message and gets AI response"""
        # Create session first
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        
        # Send message
        test_message = "Hello, what are my top deals?"
        response = session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": test_message},
            timeout=60  # AI response may take time
        )
        assert response.status_code == 200, f"Failed to send message: {response.text}"
        ai_msg = response.json()
        
        # Verify AI response structure
        assert "message_id" in ai_msg, "No message_id in AI response"
        assert "role" in ai_msg, "No role in AI response"
        assert ai_msg["role"] == "assistant", f"Expected 'assistant' role, got '{ai_msg['role']}'"
        assert "content" in ai_msg, "No content in AI response"
        assert len(ai_msg["content"]) > 0, "AI response content is empty"
        print(f"AI response: {ai_msg['content'][:100]}...")
    
    def test_06_get_messages_after_send(self, session):
        """Verify both user and AI messages are stored"""
        # Create session
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        
        # Send message
        test_message = "What is SimpliMeta AEGISAXIOM?"
        response = session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": test_message},
            timeout=60
        )
        assert response.status_code == 200
        
        # Get messages - should have user msg + AI msg = 2 messages
        msgs_resp = session.get(f"{BASE_URL}/api/chat/sessions/{session_id}/messages")
        assert msgs_resp.status_code == 200
        messages = msgs_resp.json()
        
        assert len(messages) >= 2, f"Expected at least 2 messages, got {len(messages)}"
        
        # First should be user, second should be assistant
        user_msgs = [m for m in messages if m["role"] == "user"]
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        
        assert len(user_msgs) >= 1, "No user messages found"
        assert len(assistant_msgs) >= 1, "No assistant messages found"
        assert user_msgs[0]["content"] == test_message, "User message content mismatch"
        print(f"Session has {len(messages)} messages: {len(user_msgs)} user, {len(assistant_msgs)} assistant")
    
    def test_07_delete_session(self, session):
        """DELETE /api/chat/sessions/{id} - deletes session and its messages"""
        # Create session
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        
        # Delete session
        delete_resp = session.delete(f"{BASE_URL}/api/chat/sessions/{session_id}")
        assert delete_resp.status_code == 200, f"Failed to delete session: {delete_resp.text}"
        
        # Verify session is gone - should return 404
        get_resp = session.get(f"{BASE_URL}/api/chat/sessions/{session_id}/messages")
        assert get_resp.status_code == 404, "Session should return 404 after delete"
        print(f"Session {session_id} deleted successfully")
    
    def test_08_session_title_updates_on_first_message(self, session):
        """Session title should update to message content after first message"""
        # Create session
        create_resp = session.post(f"{BASE_URL}/api/chat/sessions")
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        assert create_resp.json()["title"] == "New Chat"
        
        # Send first message
        test_message = "Show me my commission summary please"
        response = session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": test_message},
            timeout=60
        )
        assert response.status_code == 200
        
        # Get sessions and check title updated
        sessions_resp = session.get(f"{BASE_URL}/api/chat/sessions")
        assert sessions_resp.status_code == 200
        sessions = sessions_resp.json()
        
        # Find our session
        our_session = next((s for s in sessions if s["session_id"] == session_id), None)
        assert our_session is not None, "Session not found in list"
        assert our_session["title"] != "New Chat", f"Title should have updated from 'New Chat'"
        print(f"Session title updated to: {our_session['title']}")
    
    def test_09_unauthorized_access(self):
        """Verify chat endpoints require authentication"""
        # No auth - new session without login
        response = requests.get(f"{BASE_URL}/api/chat/sessions")
        assert response.status_code in [401, 403], f"Expected 401/403 without auth, got {response.status_code}"
        
        response = requests.post(f"{BASE_URL}/api/chat/sessions")
        assert response.status_code in [401, 403], f"Expected 401/403 without auth, got {response.status_code}"
        print("Unauthorized access correctly rejected")
    
    def test_10_invalid_session_access(self, session):
        """Accessing non-existent session should return 404"""
        fake_session_id = "chat_nonexistent123"
        response = session.get(f"{BASE_URL}/api/chat/sessions/{fake_session_id}/messages")
        assert response.status_code == 404, f"Expected 404 for invalid session, got {response.status_code}"
        print("Invalid session correctly returns 404")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
