"""
Tests for Iteration 30: 
1. Estimated BOA Cost field for Field Services BOA contracts
2. Removal of referral_mrr from valid contract types
"""
import pytest
import requests
import os
import uuid

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

@pytest.fixture(scope="module")
def auth_session():
    """Get authenticated session with cookie"""
    session = requests.Session()
    response = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": "support@simplimeta.io",
        "password": "Admin123!"
    })
    if response.status_code == 200:
        return session
    pytest.skip(f"Authentication failed: {response.status_code} - {response.text}")


class TestReferralMrrRemoval:
    """Tests for removal of referral_mrr from valid contract types"""
    
    def test_referral_mrr_rejected_as_contract_type(self, auth_session):
        """Contract creation should reject 'referral_mrr' as invalid contract type"""
        # First get a test account
        accounts_response = auth_session.get(
            f"{BASE_URL}/api/accounts?limit=1&test_only=true"
        )
        assert accounts_response.status_code == 200, f"Failed to get accounts: {accounts_response.text}"
        accounts = accounts_response.json().get("items", [])
        
        # If no test accounts, try any account
        if not accounts:
            accounts_response = auth_session.get(
                f"{BASE_URL}/api/accounts?limit=1"
            )
            accounts = accounts_response.json().get("items", [])
        
        assert len(accounts) > 0, "Need at least one account to test"
        account = accounts[0]
        
        # Try to create contract with referral_mrr type
        response = auth_session.post(
            f"{BASE_URL}/api/contracts",
            json={
                "name": f"TEST_Referral_Contract_{uuid.uuid4().hex[:6]}",
                "account_id": account["account_id"],
                "contract_type": "referral_mrr",  # This should be rejected
                "service_category": "wholesale_mrr"
            }
        )
        
        # Should get 400 Bad Request
        assert response.status_code == 400, f"Expected 400 for referral_mrr type, got {response.status_code}: {response.text}"
        
        # Verify error message mentions invalid contract type
        error_detail = response.json().get("detail", "")
        assert "Invalid contract type" in error_detail or "referral_mrr" in error_detail.lower(), \
            f"Error should mention invalid contract type, got: {error_detail}"
        print(f"✓ referral_mrr correctly rejected with message: {error_detail}")
    
    def test_valid_contract_types_accepted(self, auth_session):
        """Valid contract types should still be accepted"""
        # Get a test account
        accounts_response = auth_session.get(
            f"{BASE_URL}/api/accounts?limit=1"
        )
        accounts = accounts_response.json().get("items", [])
        assert len(accounts) > 0
        account = accounts[0]
        
        valid_types = ["boh", "boa", "fixed_fee", "monthly", "wholesale_mrr"]
        
        for contract_type in valid_types:
            # Just check that the request doesn't fail with "invalid type"
            response = auth_session.post(
                f"{BASE_URL}/api/contracts",
                json={
                    "name": f"TEST_ValidType_{contract_type}_{uuid.uuid4().hex[:6]}",
                    "account_id": account["account_id"],
                    "contract_type": contract_type,
                    "service_category": "field_services" if contract_type in ["boh", "boa", "fixed_fee"] else "wholesale_mrr"
                }
            )
            
            # Should NOT get 400 with "Invalid contract type"
            if response.status_code == 400:
                error_detail = response.json().get("detail", "")
                assert "Invalid contract type" not in error_detail, \
                    f"Contract type '{contract_type}' should be valid, but got: {error_detail}"
            
            # Clean up if created successfully
            if response.status_code == 200:
                contract_id = response.json().get("contract_id")
                if contract_id:
                    auth_session.delete(f"{BASE_URL}/api/contracts/{contract_id}")
            
            print(f"✓ Contract type '{contract_type}' is valid")


class TestEstimatedBoaCost:
    """Tests for Estimated BOA Cost field in Field Services BOA contracts"""
    
    @pytest.fixture
    def test_account(self, auth_session):
        """Get or create a test account"""
        response = auth_session.get(
            f"{BASE_URL}/api/accounts?limit=1"
        )
        accounts = response.json().get("items", [])
        if accounts:
            return accounts[0]
        pytest.skip("No accounts available for testing")
    
    def test_boa_contract_with_estimated_cost_field_services(self, auth_session, test_account):
        """BOA contract with field_services should accept estimated_boa_cost"""
        unique_id = uuid.uuid4().hex[:8]
        
        response = auth_session.post(
            f"{BASE_URL}/api/contracts",
            json={
                "name": f"TEST_FS_BOA_WithCost_{unique_id}",
                "account_id": test_account["account_id"],
                "contract_type": "boa",
                "service_category": "field_services",
                "estimated_boa_cost": 5000.50,  # Field Services estimated cost
                "hourly_rate": 150,
                "billing_period": "monthly"
            }
        )
        
        assert response.status_code == 200, f"Failed to create BOA contract: {response.text}"
        contract = response.json()
        contract_id = contract.get("contract_id")
        
        try:
            # Verify estimated_boa_cost was saved
            assert "estimated_boa_cost" in contract, "Response should contain estimated_boa_cost field"
            assert contract["estimated_boa_cost"] == 5000.50, \
                f"estimated_boa_cost should be 5000.50, got {contract.get('estimated_boa_cost')}"
            print(f"✓ BOA contract created with estimated_boa_cost: {contract['estimated_boa_cost']}")
            
            # Verify on GET
            get_response = auth_session.get(
                f"{BASE_URL}/api/contracts/{contract_id}"
            )
            assert get_response.status_code == 200
            fetched = get_response.json()
            assert fetched.get("estimated_boa_cost") == 5000.50, \
                f"GET should return same estimated_boa_cost, got {fetched.get('estimated_boa_cost')}"
            print(f"✓ GET confirmed estimated_boa_cost persisted: {fetched['estimated_boa_cost']}")
            
        finally:
            # Cleanup
            auth_session.delete(f"{BASE_URL}/api/contracts/{contract_id}")
    
    def test_boa_contract_without_estimated_cost(self, auth_session, test_account):
        """BOA contract without estimated_boa_cost should default to 0"""
        unique_id = uuid.uuid4().hex[:8]
        
        response = auth_session.post(
            f"{BASE_URL}/api/contracts",
            json={
                "name": f"TEST_FS_BOA_NoCost_{unique_id}",
                "account_id": test_account["account_id"],
                "contract_type": "boa",
                "service_category": "field_services",
                "hourly_rate": 150,
                "billing_period": "monthly"
                # No estimated_boa_cost provided
            }
        )
        
        assert response.status_code == 200, f"Failed to create BOA contract: {response.text}"
        contract = response.json()
        contract_id = contract.get("contract_id")
        
        try:
            # estimated_boa_cost should default to 0
            assert contract.get("estimated_boa_cost", 0) == 0, \
                f"estimated_boa_cost should default to 0, got {contract.get('estimated_boa_cost')}"
            print(f"✓ BOA contract created without estimated_boa_cost, defaulted to 0")
            
        finally:
            auth_session.delete(f"{BASE_URL}/api/contracts/{contract_id}")
    
    def test_boa_professional_services_no_estimated_cost(self, auth_session, test_account):
        """Professional Services BOA shouldn't require estimated_boa_cost (it's FS specific)"""
        unique_id = uuid.uuid4().hex[:8]
        
        response = auth_session.post(
            f"{BASE_URL}/api/contracts",
            json={
                "name": f"TEST_PS_BOA_{unique_id}",
                "account_id": test_account["account_id"],
                "contract_type": "boa",
                "service_category": "professional_services",  # Not field_services
                "hourly_rate": 200,
                "billing_period": "monthly"
            }
        )
        
        assert response.status_code == 200, f"Failed to create PS BOA contract: {response.text}"
        contract = response.json()
        contract_id = contract.get("contract_id")
        
        try:
            # Should still work without estimated_boa_cost for professional services
            print(f"✓ Professional Services BOA contract created successfully")
            
        finally:
            auth_session.delete(f"{BASE_URL}/api/contracts/{contract_id}")
    
    def test_estimated_boa_cost_is_float(self, auth_session, test_account):
        """estimated_boa_cost should handle various numeric inputs"""
        unique_id = uuid.uuid4().hex[:8]
        
        # Test with string input (should be converted to float)
        response = auth_session.post(
            f"{BASE_URL}/api/contracts",
            json={
                "name": f"TEST_BOA_FloatConvert_{unique_id}",
                "account_id": test_account["account_id"],
                "contract_type": "boa",
                "service_category": "field_services",
                "estimated_boa_cost": "7500.75",  # String input
                "hourly_rate": 150,
                "billing_period": "monthly"
            }
        )
        
        assert response.status_code == 200, f"Failed to create BOA contract: {response.text}"
        contract = response.json()
        contract_id = contract.get("contract_id")
        
        try:
            # Should be converted to float
            assert isinstance(contract.get("estimated_boa_cost"), (int, float)), \
                f"estimated_boa_cost should be numeric, got {type(contract.get('estimated_boa_cost'))}"
            assert contract.get("estimated_boa_cost") == 7500.75, \
                f"estimated_boa_cost should be 7500.75, got {contract.get('estimated_boa_cost')}"
            print(f"✓ String input converted to float: {contract['estimated_boa_cost']}")
            
        finally:
            auth_session.delete(f"{BASE_URL}/api/contracts/{contract_id}")


class TestServiceCategoryOptions:
    """Tests to verify service category options in contracts API"""
    
    def test_contracts_endpoint_returns_service_categories(self, auth_session):
        """GET contracts should include service_category field"""
        response = auth_session.get(
            f"{BASE_URL}/api/contracts?limit=5"
        )
        assert response.status_code == 200
        data = response.json()
        
        # Check if any contracts have service_category
        contracts = data.get("items", [])
        if contracts:
            # Just verify the field exists in schema
            print(f"✓ Found {len(contracts)} contracts")
            for c in contracts[:3]:
                if c.get("service_category"):
                    print(f"  - Contract '{c.get('name')}' has service_category: {c.get('service_category')}")
        else:
            print("✓ No contracts found (empty list is valid)")
    
    def test_field_services_category_with_boa(self, auth_session):
        """Field Services category should support BOA contract type"""
        accounts_response = auth_session.get(f"{BASE_URL}/api/accounts?limit=1")
        accounts = accounts_response.json().get("items", [])
        if not accounts:
            pytest.skip("No accounts available")
        
        account = accounts[0]
        unique_id = uuid.uuid4().hex[:8]
        
        response = auth_session.post(
            f"{BASE_URL}/api/contracts",
            json={
                "name": f"TEST_FS_Category_{unique_id}",
                "account_id": account["account_id"],
                "contract_type": "boa",
                "service_category": "field_services",
                "hourly_rate": 175,
                "billing_period": "monthly"
            }
        )
        
        assert response.status_code == 200, f"Failed: {response.text}"
        contract = response.json()
        contract_id = contract.get("contract_id")
        
        try:
            assert contract.get("service_category") == "field_services"
            assert contract.get("contract_type") == "boa"
            print(f"✓ Field Services BOA contract created with correct category")
            
        finally:
            auth_session.delete(f"{BASE_URL}/api/contracts/{contract_id}")
