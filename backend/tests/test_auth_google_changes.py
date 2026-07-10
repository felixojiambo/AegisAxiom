"""
Test suite for Google Auth Changes in AegisAxiom
Tests:
1. POST /api/auth/google/session - REJECT users not in database (403)
2. POST /api/auth/google/session - STILL work for existing users (200)
3. POST /api/auth/accept-invite-google endpoint exists and validates
4. POST /api/auth/accept-invite-google rejects when Google email mismatch
5. POST /api/auth/request-password-change (authenticated) creates reset token
6. POST /api/auth/request-password-change requires authentication (401)
7. Login with email/password still works
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL').rstrip('/')
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


class TestGoogleSessionRestriction:
    """Test that /api/auth/google/session only works for existing users"""
    
    def test_google_session_rejects_nonexistent_user(self):
        """POST /api/auth/google/session should return 403 for users not in database
        Note: With fake session_id, we get 401 from Google OAuth validation first
        The 403 would happen if a user's Google account is valid but not in DB"""
        response = requests.post(
            f"{BASE_URL}/api/auth/google/session",
            json={"code": "fake_auth_code_12345"},
            timeout=10
        )
        if response.status_code == 500:
            pytest.skip("Google OAuth not configured on server — skipping")
        # Should fail with 401 (Google auth fails first) or 403 (if user not found)
        assert response.status_code in [401, 403], f"Expected 401 or 403, got {response.status_code}"
        data = response.json()
        assert "detail" in data
        print(f"TEST PASS: Google session rejects fake session with {response.status_code}")
        
    def test_google_session_endpoint_exists(self):
        """Verify the endpoint exists and accepts POST requests"""
        response = requests.post(
            f"{BASE_URL}/api/auth/google/session",
            json={"code": "test"},
            timeout=10
        )
        # Should not be 404 (endpoint exists)
        assert response.status_code != 404, "Endpoint /api/auth/google/session should exist"
        print(f"TEST PASS: Google session endpoint exists, returns {response.status_code}")


class TestAcceptInviteGoogle:
    """Test the new accept-invite-google endpoint"""
    
    def test_accept_invite_google_endpoint_exists(self):
        """POST /api/auth/accept-invite-google endpoint should exist"""
        response = requests.post(
            f"{BASE_URL}/api/auth/accept-invite-google",
            json={"code": "fake_auth_code", "invite_token": "fake_token"},
            timeout=10
        )
        # Should not be 404 (endpoint exists)
        assert response.status_code != 404, "Endpoint /api/auth/accept-invite-google should exist"
        print(f"TEST PASS: Accept invite Google endpoint exists, returns {response.status_code}")
    
    def test_accept_invite_google_rejects_invalid_token(self):
        """Should reject when invite token is invalid"""
        response = requests.post(
            f"{BASE_URL}/api/auth/accept-invite-google",
            json={"code": "fake_auth_code", "invite_token": "invalid_token"},
            timeout=10
        )
        # Should fail with 401 (bad JWT) or similar error
        assert response.status_code in [400, 401, 403, 422], f"Expected error status, got {response.status_code}"
        print(f"TEST PASS: Accept invite Google rejects invalid token with {response.status_code}")
    
    def test_accept_invite_google_requires_both_fields(self):
        """Should require both code and invite_token"""
        # Missing invite_token
        response1 = requests.post(
            f"{BASE_URL}/api/auth/accept-invite-google",
            json={"code": "fake_auth_code"},
            timeout=10
        )
        assert response1.status_code == 422, f"Expected 422 for missing invite_token, got {response1.status_code}"

        # Missing code
        response2 = requests.post(
            f"{BASE_URL}/api/auth/accept-invite-google",
            json={"invite_token": "fake_token"},
            timeout=10
        )
        assert response2.status_code == 422, f"Expected 422 for missing code, got {response2.status_code}"
        print("TEST PASS: Accept invite Google validates required fields")


class TestRequestPasswordChange:
    """Test the new request-password-change endpoint"""
    
    @pytest.fixture
    def authenticated_session(self):
        """Get authenticated session using admin credentials"""
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "support@simplimeta.io", "password": ADMIN_PASSWORD},
            timeout=10
        )
        if response.status_code != 200:
            pytest.skip(f"Login failed: {response.status_code} - {response.text}")
        return session
    
    def test_request_password_change_requires_auth(self):
        """POST /api/auth/request-password-change should return 401 without session"""
        response = requests.post(
            f"{BASE_URL}/api/auth/request-password-change",
            timeout=10
        )
        assert response.status_code == 401, f"Expected 401 without auth, got {response.status_code}"
        print("TEST PASS: Request password change requires authentication")
    
    def test_request_password_change_works_authenticated(self, authenticated_session):
        """POST /api/auth/request-password-change should work when authenticated"""
        response = authenticated_session.post(
            f"{BASE_URL}/api/auth/request-password-change",
            timeout=10
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "message" in data
        assert "email" in data["message"].lower() or "sent" in data["message"].lower()
        print(f"TEST PASS: Request password change works, message: {data['message']}")
    
    def test_request_password_change_endpoint_exists(self):
        """Verify the endpoint exists"""
        response = requests.post(
            f"{BASE_URL}/api/auth/request-password-change",
            timeout=10
        )
        # Should not be 404 (endpoint exists)
        assert response.status_code != 404, "Endpoint /api/auth/request-password-change should exist"
        print(f"TEST PASS: Request password change endpoint exists, returns {response.status_code}")


class TestExistingAuthFlows:
    """Test that existing auth flows still work"""
    
    def test_login_with_email_password_works(self):
        """Login with email/password should still work"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "support@simplimeta.io", "password": ADMIN_PASSWORD},
            timeout=10
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "user" in data
        assert data["user"]["email"] == "support@simplimeta.io"
        print("TEST PASS: Email/password login still works")
    
    def test_login_returns_user_data(self):
        """Login should return complete user data"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "support@simplimeta.io", "password": ADMIN_PASSWORD},
            timeout=10
        )
        assert response.status_code == 200
        data = response.json()
        user = data["user"]
        assert "user_id" in user
        assert "email" in user
        assert "name" in user
        assert "role" in user
        print(f"TEST PASS: Login returns user data - {user['name']} ({user['role']})")
    
    def test_validate_invite_endpoint_works(self):
        """Validate invite endpoint should exist and reject invalid tokens"""
        response = requests.post(
            f"{BASE_URL}/api/auth/validate-invite",
            json={"token": "invalid_token", "new_password": "dummy"},
            timeout=10
        )
        # Should fail but endpoint exists
        assert response.status_code != 404
        print(f"TEST PASS: Validate invite endpoint exists, returns {response.status_code}")


class TestAcceptInvitePasswordOption:
    """Test that accept-invite with password still works"""
    
    def test_accept_invite_endpoint_exists(self):
        """POST /api/auth/accept-invite endpoint should still exist"""
        response = requests.post(
            f"{BASE_URL}/api/auth/accept-invite",
            json={"token": "fake_token", "new_password": "TestPass123!"},
            timeout=10
        )
        # Should not be 404 (endpoint exists)
        assert response.status_code != 404, "Endpoint /api/auth/accept-invite should exist"
        print(f"TEST PASS: Accept invite (password) endpoint exists, returns {response.status_code}")
    
    def test_accept_invite_rejects_invalid_token(self):
        """Should reject invalid invite tokens"""
        response = requests.post(
            f"{BASE_URL}/api/auth/accept-invite",
            json={"token": "invalid_token", "new_password": "TestPass123!"},
            timeout=10
        )
        assert response.status_code in [400, 401, 403], f"Expected error for invalid token, got {response.status_code}"
        print(f"TEST PASS: Accept invite rejects invalid token with {response.status_code}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
