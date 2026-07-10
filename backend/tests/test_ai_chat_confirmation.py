"""
AI Chat Confirmation Flow Tests
Tests for the two-pass AI-driven query interpretation model with confirmation workflow.

Features tested:
1. Deals question → confirmation with bold text → say 'yes' → get data answer
2. Commissions question → confirmation with bold text → say 'yes' → get data answer  
3. Rejection flow: question → confirmation → say 'no' → get rephrase message
4. New query override: question → confirmation → new question → NEW confirmation
5. General greeting bypass: 'hello' → direct answer, NO confirmation
6. Confirmation message content: should include bolded filter keywords
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

class TestAIChatConfirmationFlow:
    """Test the AI chat confirmation workflow"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookie"""
        self.session = requests.Session()
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "support@simplimeta.io", "password": ADMIN_PASSWORD},
            headers={"Content-Type": "application/json"}
        )
        assert login_response.status_code == 200, f"Login failed: {login_response.text}"
        yield
        # Cleanup - sessions are stored in DB, can be cleaned up later
    
    def test_deals_question_returns_confirmation(self):
        """Test: Deals question returns confirmation message with bold text"""
        # Create a new chat session
        session_response = self.session.post(f"{BASE_URL}/api/chat/sessions")
        assert session_response.status_code == 200
        session_id = session_response.json()["session_id"]
        
        # Send a deals question
        msg_response = self.session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": "What deals did Cody close in February 2025?"}
        )
        assert msg_response.status_code == 200
        
        # Verify confirmation message
        content = msg_response.json()["content"]
        assert "Just to confirm" in content or "confirm" in content.lower(), \
            f"Expected confirmation message, got: {content}"
        
        # Check for bold markdown syntax (** markers)
        assert "**" in content, f"Expected bold text markers in confirmation: {content}"
        
    def test_commissions_question_returns_confirmation(self):
        """Test: Commissions question returns confirmation message with bold text"""
        # Create session
        session_response = self.session.post(f"{BASE_URL}/api/chat/sessions")
        assert session_response.status_code == 200
        session_id = session_response.json()["session_id"]
        
        # Send a commissions question
        msg_response = self.session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": "Show me all commissions owed to Ryan"}
        )
        assert msg_response.status_code == 200
        
        # Verify confirmation message
        content = msg_response.json()["content"]
        assert "Just to confirm" in content or "confirm" in content.lower()
        assert "**" in content, "Expected bold text in confirmation"
        
    def test_yes_confirmation_executes_query(self):
        """Test: Saying 'yes' after confirmation executes the query"""
        # Create session
        session_response = self.session.post(f"{BASE_URL}/api/chat/sessions")
        assert session_response.status_code == 200
        session_id = session_response.json()["session_id"]
        
        # Send a deals question
        self.session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": "What deals are in the Closed Won stage?"}
        )
        time.sleep(1)  # Allow time for LLM
        
        # Confirm with 'yes'
        confirm_response = self.session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": "yes"}
        )
        assert confirm_response.status_code == 200
        
        content = confirm_response.json()["content"]
        # Should NOT be another confirmation question
        assert "Just to confirm" not in content, \
            f"Expected data response, got another confirmation: {content}"
        
    def test_no_rejection_returns_rephrase_message(self):
        """Test: Saying 'no' after confirmation asks user to rephrase"""
        # Create session
        session_response = self.session.post(f"{BASE_URL}/api/chat/sessions")
        assert session_response.status_code == 200
        session_id = session_response.json()["session_id"]
        
        # Send a question
        self.session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": "Show me all commissions owed to Ryan"}
        )
        time.sleep(1)
        
        # Reject with 'no'
        reject_response = self.session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": "no"}
        )
        assert reject_response.status_code == 200
        
        content = reject_response.json()["content"]
        assert "rephrase" in content.lower() or "try again" in content.lower(), \
            f"Expected rephrase message, got: {content}"
        
    def test_new_query_overrides_pending_confirmation(self):
        """Test: Sending a new question clears pending query and starts new cycle"""
        # Create session
        session_response = self.session.post(f"{BASE_URL}/api/chat/sessions")
        assert session_response.status_code == 200
        session_id = session_response.json()["session_id"]
        
        # Send first question - should get confirmation
        first_response = self.session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": "What deals are in the Closed Won stage?"}
        )
        assert "confirm" in first_response.json()["content"].lower() or \
               "**" in first_response.json()["content"]
        
        time.sleep(1)
        
        # Send DIFFERENT question instead of yes/no
        second_response = self.session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": "Actually, show me all unpaid commissions"}
        )
        assert second_response.status_code == 200
        
        content = second_response.json()["content"]
        # Should get NEW confirmation about commissions, not execute deals query
        assert "commission" in content.lower(), \
            f"Expected new confirmation about commissions, got: {content}"
        
    def test_general_greeting_bypasses_confirmation(self):
        """Test: General greeting gets direct response, no confirmation needed"""
        # Create session
        session_response = self.session.post(f"{BASE_URL}/api/chat/sessions")
        assert session_response.status_code == 200
        session_id = session_response.json()["session_id"]
        
        # Send greeting
        greeting_response = self.session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": "Hello"}
        )
        assert greeting_response.status_code == 200
        
        content = greeting_response.json()["content"]
        # Should NOT ask for confirmation
        assert "Just to confirm" not in content, \
            f"Greeting should not trigger confirmation, got: {content}"
        # Should give a helpful response
        assert len(content) > 20, "Expected substantive greeting response"
        
    def test_confirmation_contains_bolded_keywords(self):
        """Test: Confirmation message has filter keywords in bold markdown"""
        # Create session
        session_response = self.session.post(f"{BASE_URL}/api/chat/sessions")
        assert session_response.status_code == 200
        session_id = session_response.json()["session_id"]
        
        # Send a question with multiple filters
        msg_response = self.session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": "Show me all deals over $50,000 in Q3 2025"}
        )
        assert msg_response.status_code == 200
        
        content = msg_response.json()["content"]
        
        # Count bold markers - should have multiple
        bold_count = content.count("**")
        assert bold_count >= 2, f"Expected at least 2 bold markers, got {bold_count}: {content}"
        
    def test_multiple_confirmation_phrases_work(self):
        """Test: Various confirmation phrases (yeah, yep, correct) work"""
        # Create session
        session_response = self.session.post(f"{BASE_URL}/api/chat/sessions")
        assert session_response.status_code == 200
        session_id = session_response.json()["session_id"]
        
        # Send question
        self.session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": "What deals are in my pipeline?"}
        )
        time.sleep(1)
        
        # Confirm with 'yep'
        confirm_response = self.session.post(
            f"{BASE_URL}/api/chat/sessions/{session_id}/messages",
            json={"content": "yep"}
        )
        assert confirm_response.status_code == 200
        
        content = confirm_response.json()["content"]
        # Should execute query, not ask for confirmation again
        assert "Just to confirm" not in content, \
            f"'yep' should work as confirmation, got: {content}"


class TestChatSessionManagement:
    """Test chat session CRUD operations"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookie"""
        self.session = requests.Session()
        login_response = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "support@simplimeta.io", "password": ADMIN_PASSWORD},
            headers={"Content-Type": "application/json"}
        )
        assert login_response.status_code == 200
        
    def test_create_session(self):
        """Test creating a new chat session"""
        response = self.session.post(f"{BASE_URL}/api/chat/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert data["title"] == "New Chat"
        
    def test_list_sessions(self):
        """Test listing user's chat sessions"""
        response = self.session.get(f"{BASE_URL}/api/chat/sessions")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        
    def test_get_messages(self):
        """Test getting messages for a session"""
        # Create session first
        session_response = self.session.post(f"{BASE_URL}/api/chat/sessions")
        session_id = session_response.json()["session_id"]
        
        # Get messages
        response = self.session.get(f"{BASE_URL}/api/chat/sessions/{session_id}/messages")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        
    def test_delete_session(self):
        """Test deleting a chat session"""
        # Create session first
        session_response = self.session.post(f"{BASE_URL}/api/chat/sessions")
        session_id = session_response.json()["session_id"]
        
        # Delete it
        delete_response = self.session.delete(f"{BASE_URL}/api/chat/sessions/{session_id}")
        assert delete_response.status_code == 200
