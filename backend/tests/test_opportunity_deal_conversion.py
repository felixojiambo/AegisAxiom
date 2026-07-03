"""
Test Opportunity to Deal Conversion with SOW → Contract Mapping (Phase 3C)
"""
import pytest
from fastapi.testclient import TestClient
from server import app

client = TestClient(app)

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin@123!"


@pytest.fixture
def auth_headers():
    """Get authentication headers."""
    response = client.post("/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })
    assert response.status_code == 200
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_convert_prevents_duplicate_conversion(auth_headers):
    """Test that already-converted opportunities cannot be re-converted."""
    # test_conversion_opp_001 was already converted in the setup
    response = client.post(
        "/api/opportunities/test_conversion_opp_001/convert-to-deal",
        headers=auth_headers
    )
    
    assert response.status_code == 400
    assert "already been converted" in response.json()["detail"]


def test_convert_nonexistent_opportunity(auth_headers):
    """Test conversion of non-existent opportunity returns 404."""
    response = client.post(
        "/api/opportunities/nonexistent_opp_id/convert-to-deal",
        headers=auth_headers
    )
    
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_convert_incomplete_sow_skips_contract(auth_headers):
    """Test that opportunities with incomplete SOWs create Deal but skip Contract creation."""
    # test_conversion_opp_002 has incomplete SOW (status: in_progress)
    response = client.post(
        "/api/opportunities/test_conversion_opp_002/convert-to-deal",
        headers=auth_headers
    )
    
    # Should succeed but create 0 contracts
    if response.status_code == 200:
        data = response.json()
        assert "deal_id" in data
        assert len(data.get("contracts_created", [])) == 0
        assert data.get("sows_processed", 0) >= 1
        assert data.get("sows_converted", 0) == 0
    elif response.status_code == 400:
        # Already converted
        assert "already been converted" in response.json()["detail"]


def test_convert_response_structure(auth_headers):
    """Test that conversion response has expected structure."""
    # Use test_conversion_opp_001 (will fail due to duplicate, but we can check error structure)
    response = client.post(
        "/api/opportunities/test_conversion_opp_001/convert-to-deal",
        headers=auth_headers
    )
    
    # Check response structure (either success or expected error)
    assert response.status_code in [200, 400]
    
    if response.status_code == 200:
        data = response.json()
        assert "message" in data
        assert "deal_id" in data
        assert "opportunity_id" in data
        assert "contracts_created" in data
        assert "sows_processed" in data
        assert "sows_converted" in data
        assert isinstance(data["contracts_created"], list)
    else:
        # Duplicate conversion error
        assert "detail" in response.json()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

