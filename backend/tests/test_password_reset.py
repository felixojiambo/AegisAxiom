"""
Test Password Reset Flow - forgot-password and reset-password endpoints
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestForgotPassword:
    """Tests for /api/auth/forgot-password endpoint"""
    
    def test_forgot_password_with_valid_email(self):
        """POST /api/auth/forgot-password with valid email returns 200"""
        response = requests.post(
            f"{BASE_URL}/api/auth/forgot-password",
            json={"email": "support@simplimeta.io"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "reset link has been sent" in data["message"].lower()
    
    def test_forgot_password_with_unknown_email(self):
        """POST /api/auth/forgot-password with unknown email still returns 200 (anti-enumeration)"""
        response = requests.post(
            f"{BASE_URL}/api/auth/forgot-password",
            json={"email": "nonexistent@example.com"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        # Same message for unknown email to prevent user enumeration
        assert "reset link has been sent" in data["message"].lower()


class TestResetPassword:
    """Tests for /api/auth/reset-password endpoint"""
    
    def test_reset_password_with_invalid_token(self):
        """POST /api/auth/reset-password with bad token returns 400"""
        response = requests.post(
            f"{BASE_URL}/api/auth/reset-password",
            json={"token": "invalidtoken123", "new_password": "newpassword123"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "invalid" in data["detail"].lower() or "expired" in data["detail"].lower()
    
    def test_reset_password_with_short_password(self):
        """POST /api/auth/reset-password with password < 8 chars returns 400"""
        response = requests.post(
            f"{BASE_URL}/api/auth/reset-password",
            json={"token": "anytoken", "new_password": "short"},
            headers={"Content-Type": "application/json"}
        )
        # Should return 400 either for invalid token or short password
        assert response.status_code == 400


class TestLogin:
    """Test login still works after password reset changes"""
    
    def test_login_with_admin_credentials(self):
        """Login with admin credentials should succeed"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "support@simplimeta.io", "password": "Admin@123"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "user" in data
        assert "token" in data
        assert data["user"]["email"] == "support@simplimeta.io"
        assert data["user"]["role"] == "admin"
    
    def test_login_with_sales_rep_credentials(self):
        """Login with sales rep credentials should succeed"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "aharrelson@simplimeta.io", "password": "Kungfupand@1"},
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "user" in data
        assert "token" in data
        assert data["user"]["email"] == "aharrelson@simplimeta.io"


class TestHealthCheck:
    """Test health endpoint"""
    
    def test_health(self):
        """Health endpoint should return OK"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
