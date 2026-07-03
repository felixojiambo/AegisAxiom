"""
Test Fixed Fee Contract Creation and Update - Milestone Percentage Float Casting

Tests the bug fix for TypeError when saving Fixed Fee contracts with milestones.
The fix ensures milestone percentages are cast to float in both POST and PUT endpoints.

Test scenarios:
1. POST /api/contracts - Create Fixed Fee contract with milestones (string percentages)
2. PUT /api/contracts/{id} - Update Fixed Fee contract milestones (string percentages)
3. Verify milestone percentages are stored as floats
4. Verify existing Wayne Enterprises Fixed Fee contract integrity
"""

import pytest
import requests
import os
import time

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# Test credentials
ADMIN_EMAIL = "support@simplimeta.io"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


class TestFixedFeeContractMilestoneFloatCasting:
    """Tests for Fixed Fee contract milestone percentage float casting bug fix"""

    @pytest.fixture(scope="class")
    def admin_token(self):
        """Get admin authentication token"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        if response.status_code == 429:
            time.sleep(2)
            response = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            )
        assert response.status_code == 200, f"Admin login failed: {response.text}"
        data = response.json()
        return data.get("access_token") or data.get("token")

    @pytest.fixture(scope="class")
    def auth_headers(self, admin_token):
        """Headers with authentication"""
        return {
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
        }

    @pytest.fixture(scope="class")
    def wayne_enterprises_account_id(self, auth_headers):
        """Get Wayne Enterprises account ID"""
        response = requests.get(
            f"{BASE_URL}/api/accounts",
            headers=auth_headers,
        )
        assert response.status_code == 200, f"Failed to get accounts: {response.text}"
        accounts = response.json().get("items", [])
        
        # Find Wayne Enterprises account
        wayne_account = None
        for acc in accounts:
            if "Wayne" in acc.get("name", ""):
                wayne_account = acc
                break
        
        return wayne_account.get("account_id") if wayne_account else None

    # Test 1: Create Fixed Fee contract with STRING milestone percentages (should cast to float)
    def test_create_fixed_fee_contract_with_string_percentages(self, auth_headers, wayne_enterprises_account_id):
        """POST /api/contracts - Create Fixed Fee contract with string milestone percentages"""
        contract_data = {
            "name": "TEST_Fixed_Fee_String_Percentages",
            "account_id": wayne_enterprises_account_id or "test_account_001",
            "contract_type": "fixed_fee",
            "total_amount": 100000,
            "start_date": "2025-01-01",
            "end_date": "2025-12-31",
            "milestones": [
                {"name": "Phase 1 - Planning", "percentage": "25"},  # String percentage
                {"name": "Phase 2 - Development", "percentage": "50"},  # String percentage
                {"name": "Phase 3 - Delivery", "percentage": "25"},  # String percentage
            ],
        }

        response = requests.post(
            f"{BASE_URL}/api/contracts",
            json=contract_data,
            headers=auth_headers,
        )
        
        assert response.status_code == 200, f"Failed to create contract: {response.text}"
        contract = response.json()

        # Verify contract was created
        assert contract.get("contract_id"), "Contract ID not returned"
        assert contract.get("contract_type") == "fixed_fee"
        assert contract.get("total_amount") == 100000

        # Verify milestones were created with float percentages
        milestones = contract.get("milestones", [])
        assert len(milestones) == 3, "Expected 3 milestones"
        
        for milestone in milestones:
            assert isinstance(milestone.get("percentage"), (int, float)), \
                f"Milestone percentage should be numeric, got {type(milestone.get('percentage'))}"
            assert milestone.get("id"), "Milestone should have an ID"
            assert milestone.get("status") == "pending", "Milestone status should be 'pending'"

        # Verify percentage values
        percentages = [m.get("percentage") for m in milestones]
        assert 25.0 in percentages or 25 in percentages
        assert 50.0 in percentages or 50 in percentages
        assert sum(percentages) == 100, f"Milestone percentages should sum to 100, got {sum(percentages)}"

        print(f"✓ Created Fixed Fee contract {contract.get('contract_id')} with float percentages")
        
        # Store contract_id for cleanup
        self.__class__.created_contract_id_1 = contract.get("contract_id")
        return contract

    # Test 2: Create Fixed Fee contract with INTEGER milestone percentages
    def test_create_fixed_fee_contract_with_int_percentages(self, auth_headers, wayne_enterprises_account_id):
        """POST /api/contracts - Create Fixed Fee contract with integer milestone percentages"""
        contract_data = {
            "name": "TEST_Fixed_Fee_Int_Percentages",
            "account_id": wayne_enterprises_account_id or "test_account_001",
            "contract_type": "fixed_fee",
            "total_amount": 200000,
            "start_date": "2025-01-01",
            "end_date": "2025-06-30",
            "milestones": [
                {"name": "Kickoff", "percentage": 10},  # Integer
                {"name": "Midpoint", "percentage": 40},  # Integer
                {"name": "UAT", "percentage": 30},  # Integer
                {"name": "Go Live", "percentage": 20},  # Integer
            ],
        }

        response = requests.post(
            f"{BASE_URL}/api/contracts",
            json=contract_data,
            headers=auth_headers,
        )
        
        assert response.status_code == 200, f"Failed to create contract: {response.text}"
        contract = response.json()

        # Verify milestones
        milestones = contract.get("milestones", [])
        assert len(milestones) == 4, "Expected 4 milestones"
        
        for milestone in milestones:
            assert isinstance(milestone.get("percentage"), (int, float)), \
                f"Milestone percentage should be numeric"
        
        # Verify calculated amounts
        for milestone in milestones:
            expected_amount = 200000 * (milestone["percentage"] / 100)
            # Amount may or may not be set depending on implementation
            if "amount" in milestone:
                assert abs(milestone["amount"] - expected_amount) < 0.01, \
                    f"Milestone amount mismatch: expected {expected_amount}, got {milestone.get('amount')}"

        print(f"✓ Created Fixed Fee contract {contract.get('contract_id')} with integer percentages")
        
        self.__class__.created_contract_id_2 = contract.get("contract_id")
        return contract

    # Test 3: Update Fixed Fee contract with STRING milestone percentages
    def test_update_fixed_fee_contract_with_string_percentages(self, auth_headers):
        """PUT /api/contracts/{id} - Update Fixed Fee contract with string milestone percentages"""
        # Use the contract created in test 1
        contract_id = getattr(self.__class__, "created_contract_id_1", None)
        if not contract_id:
            pytest.skip("No contract created in previous test")

        update_data = {
            "milestones": [
                {"name": "Phase 1 - Updated", "percentage": "30"},  # Changed from 25
                {"name": "Phase 2 - Updated", "percentage": "40"},  # Changed from 50
                {"name": "Phase 3 - Updated", "percentage": "30"},  # Changed from 25
            ],
            "total_amount": 150000,  # Updated amount
        }

        response = requests.put(
            f"{BASE_URL}/api/contracts/{contract_id}",
            json=update_data,
            headers=auth_headers,
        )
        
        assert response.status_code == 200, f"Failed to update contract: {response.text}"
        contract = response.json()

        # Verify milestones were updated with float percentages
        milestones = contract.get("milestones", [])
        assert len(milestones) == 3, "Expected 3 milestones"
        
        for milestone in milestones:
            assert isinstance(milestone.get("percentage"), (int, float)), \
                f"Milestone percentage should be numeric after update"
        
        # Verify updated percentages
        percentages = [m.get("percentage") for m in milestones]
        assert sum(percentages) == 100, f"Milestone percentages should sum to 100"

        print(f"✓ Updated Fixed Fee contract {contract_id} with string percentages successfully")
        return contract

    # Test 4: Update Fixed Fee contract with DECIMAL milestone percentages
    def test_update_fixed_fee_contract_with_decimal_percentages(self, auth_headers):
        """PUT /api/contracts/{id} - Update Fixed Fee contract with decimal milestone percentages"""
        contract_id = getattr(self.__class__, "created_contract_id_2", None)
        if not contract_id:
            pytest.skip("No contract created in previous test")

        update_data = {
            "milestones": [
                {"name": "Kickoff", "percentage": "12.5"},  # Decimal string
                {"name": "Design Complete", "percentage": "25"},
                {"name": "Development Complete", "percentage": "37.5"},  # Decimal string
                {"name": "Go Live", "percentage": "25"},
            ],
        }

        response = requests.put(
            f"{BASE_URL}/api/contracts/{contract_id}",
            json=update_data,
            headers=auth_headers,
        )
        
        assert response.status_code == 200, f"Failed to update contract: {response.text}"
        contract = response.json()

        # Verify milestones
        milestones = contract.get("milestones", [])
        for milestone in milestones:
            assert isinstance(milestone.get("percentage"), float), \
                f"Milestone percentage should be float"
        
        percentages = [m.get("percentage") for m in milestones]
        assert abs(sum(percentages) - 100) < 0.01, f"Percentages should sum to 100"

        print(f"✓ Updated Fixed Fee contract {contract_id} with decimal percentages")
        return contract

    # Test 5: Verify existing Wayne Enterprises Fixed Fee contract integrity
    def test_verify_wayne_enterprises_fixed_fee_contract(self, auth_headers, wayne_enterprises_account_id):
        """Verify existing Wayne Enterprises Fixed Fee contract data integrity"""
        if not wayne_enterprises_account_id:
            pytest.skip("Wayne Enterprises account not found")

        # Get contracts for Wayne Enterprises
        response = requests.get(
            f"{BASE_URL}/api/contracts?account_id={wayne_enterprises_account_id}&contract_type=fixed_fee",
            headers=auth_headers,
        )
        
        assert response.status_code == 200, f"Failed to get contracts: {response.text}"
        contracts = response.json().get("items", [])
        
        if not contracts:
            print("Note: No existing Fixed Fee contracts found for Wayne Enterprises")
            return
        
        # Find the Justice League contract or any Fixed Fee contract
        fixed_fee_contract = None
        for contract in contracts:
            if "Justice League" in contract.get("name", "") or contract.get("contract_type") == "fixed_fee":
                fixed_fee_contract = contract
                break
        
        if fixed_fee_contract:
            # Verify contract structure
            assert fixed_fee_contract.get("contract_type") == "fixed_fee"
            assert "total_amount" in fixed_fee_contract or "fixed_amount" in fixed_fee_contract
            
            milestones = fixed_fee_contract.get("milestones", [])
            if milestones:
                # Verify milestone percentages are numeric
                for milestone in milestones:
                    pct = milestone.get("percentage")
                    assert isinstance(pct, (int, float)), \
                        f"Milestone percentage should be numeric, got {type(pct)}: {pct}"
                
                # Verify milestones have expected structure
                percentages = [m.get("percentage", 0) for m in milestones]
                total_pct = sum(percentages)
                print(f"✓ Wayne Enterprises Fixed Fee contract verified:")
                print(f"  - Contract: {fixed_fee_contract.get('name')}")
                print(f"  - Total Amount: ${fixed_fee_contract.get('total_amount', 0):,.2f}")
                print(f"  - Milestones: {len(milestones)}")
                print(f"  - Milestone percentages: {percentages} (total: {total_pct}%)")
        else:
            print("Note: No Fixed Fee contract found with 'Justice League' name")

    # Test 6: Create Fixed Fee contract with empty/null milestones (edge case)
    def test_create_fixed_fee_contract_no_milestones(self, auth_headers, wayne_enterprises_account_id):
        """POST /api/contracts - Create Fixed Fee contract without milestones"""
        contract_data = {
            "name": "TEST_Fixed_Fee_No_Milestones",
            "account_id": wayne_enterprises_account_id or "test_account_001",
            "contract_type": "fixed_fee",
            "total_amount": 50000,
            "start_date": "2025-01-01",
            "milestones": [],  # Empty milestones
        }

        response = requests.post(
            f"{BASE_URL}/api/contracts",
            json=contract_data,
            headers=auth_headers,
        )
        
        assert response.status_code == 200, f"Failed to create contract: {response.text}"
        contract = response.json()

        assert contract.get("contract_type") == "fixed_fee"
        assert contract.get("milestones") == []

        print(f"✓ Created Fixed Fee contract {contract.get('contract_id')} without milestones")
        
        self.__class__.created_contract_id_3 = contract.get("contract_id")
        return contract

    # Test 7: Update Fixed Fee contract adding milestones where none existed
    def test_update_add_milestones_to_empty_contract(self, auth_headers):
        """PUT /api/contracts/{id} - Add milestones to contract that had none"""
        contract_id = getattr(self.__class__, "created_contract_id_3", None)
        if not contract_id:
            pytest.skip("No contract created in previous test")

        update_data = {
            "milestones": [
                {"name": "Single Milestone", "percentage": "100"},  # String
            ],
        }

        response = requests.put(
            f"{BASE_URL}/api/contracts/{contract_id}",
            json=update_data,
            headers=auth_headers,
        )
        
        assert response.status_code == 200, f"Failed to update contract: {response.text}"
        contract = response.json()

        milestones = contract.get("milestones", [])
        assert len(milestones) == 1
        assert milestones[0].get("percentage") == 100.0 or milestones[0].get("percentage") == 100

        print(f"✓ Added milestones to contract {contract_id}")
        return contract

    # Cleanup - Delete test contracts
    def test_cleanup_test_contracts(self, auth_headers):
        """Delete all test contracts created during testing"""
        contract_ids = [
            getattr(self.__class__, "created_contract_id_1", None),
            getattr(self.__class__, "created_contract_id_2", None),
            getattr(self.__class__, "created_contract_id_3", None),
        ]
        
        for contract_id in contract_ids:
            if contract_id:
                response = requests.delete(
                    f"{BASE_URL}/api/contracts/{contract_id}",
                    headers=auth_headers,
                )
                if response.status_code in [200, 204]:
                    print(f"✓ Deleted test contract {contract_id}")
                else:
                    print(f"Warning: Failed to delete contract {contract_id}: {response.status_code}")


# Standalone test functions for quick verification
def test_fixed_fee_post_no_typeerror():
    """Quick standalone test to verify POST doesn't throw TypeError"""
    session = requests.Session()
    
    # Login
    login_response = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    if login_response.status_code == 429:
        time.sleep(2)
        login_response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
    
    assert login_response.status_code == 200, f"Login failed: {login_response.text}"
    token = login_response.json().get("access_token") or login_response.json().get("token")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Create Fixed Fee contract with string percentages (this was causing TypeError)
    contract_data = {
        "name": "TEST_TypeError_Check_POST",
        "contract_type": "fixed_fee",
        "total_amount": 75000,
        "milestones": [
            {"name": "Milestone 1", "percentage": "33.33"},
            {"name": "Milestone 2", "percentage": "33.33"},
            {"name": "Milestone 3", "percentage": "33.34"},
        ],
    }

    response = session.post(
        f"{BASE_URL}/api/contracts",
        json=contract_data,
        headers=headers,
    )
    
    # Should NOT get 500 Internal Server Error (TypeError)
    assert response.status_code != 500, f"Server error (likely TypeError): {response.text}"
    assert response.status_code == 200, f"Unexpected error: {response.status_code} - {response.text}"
    
    contract = response.json()
    print(f"✓ POST /api/contracts succeeded - no TypeError")
    print(f"  Contract ID: {contract.get('contract_id')}")
    
    # Cleanup
    if contract.get("contract_id"):
        session.delete(
            f"{BASE_URL}/api/contracts/{contract.get('contract_id')}",
            headers=headers,
        )
        print(f"✓ Cleaned up test contract")


def test_fixed_fee_put_no_typeerror():
    """Quick standalone test to verify PUT doesn't throw TypeError"""
    session = requests.Session()
    
    # Login
    login_response = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    if login_response.status_code == 429:
        time.sleep(2)
        login_response = session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
    
    assert login_response.status_code == 200, f"Login failed: {login_response.text}"
    token = login_response.json().get("access_token") or login_response.json().get("token")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # First create a contract with numeric percentages
    contract_data = {
        "name": "TEST_TypeError_Check_PUT",
        "contract_type": "fixed_fee",
        "total_amount": 80000,
        "milestones": [
            {"name": "Milestone A", "percentage": 50},
            {"name": "Milestone B", "percentage": 50},
        ],
    }

    create_response = session.post(
        f"{BASE_URL}/api/contracts",
        json=contract_data,
        headers=headers,
    )
    assert create_response.status_code == 200, f"Failed to create contract: {create_response.text}"
    contract_id = create_response.json().get("contract_id")
    
    # Now UPDATE with string percentages (this was causing TypeError)
    update_data = {
        "milestones": [
            {"name": "Updated Milestone 1", "percentage": "25"},
            {"name": "Updated Milestone 2", "percentage": "50"},
            {"name": "Updated Milestone 3", "percentage": "25"},
        ],
    }

    update_response = session.put(
        f"{BASE_URL}/api/contracts/{contract_id}",
        json=update_data,
        headers=headers,
    )
    
    # Should NOT get 500 Internal Server Error (TypeError)
    assert update_response.status_code != 500, f"Server error (likely TypeError): {update_response.text}"
    assert update_response.status_code == 200, f"Unexpected error: {update_response.status_code} - {update_response.text}"
    
    print(f"✓ PUT /api/contracts/{contract_id} succeeded - no TypeError")
    
    # Cleanup
    session.delete(
        f"{BASE_URL}/api/contracts/{contract_id}",
        headers=headers,
    )
    print(f"✓ Cleaned up test contract")
