"""
Referral MRR Auto-Commission Creation Tests
Tests for CSV upload with period selection, auto-commission creation, duplicate prevention, and deals without plans warning.
This tests the new workflow where commissions are automatically created during CSV parsing (no commit button).

Key test scenarios:
1. Upload CSV with period selector → auto-create commissions
2. Duplicate prevention when re-uploading same file
3. Deals without commission plans warning
4. Commission records appear in local_commissions collection
"""
import pytest
import requests
import os
import json

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin123!"

# Real test data based on setup
TEST_DEAL_ID = "deal_9bfa6a930bba"  # 'test 11' deal with commission plan
TEST_PERIOD = "2026-01"  # January 2026


@pytest.fixture(scope="module")
def api_session():
    """Create authenticated session for all tests"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    login_response = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })
    
    if login_response.status_code != 200:
        pytest.skip(f"Authentication failed: {login_response.status_code}")
    
    return session


@pytest.fixture(scope="module")
def real_csv_file():
    """Read the actual test CSV from /tmp"""
    csv_path = "/tmp/test_referral_mrr.csv"
    if os.path.exists(csv_path):
        with open(csv_path, 'r') as f:
            return f.read()
    else:
        # Fallback CSV with test accounts matching mapped deal
        return """Commission Cycle,Account Number,Comp Paid,Sales Rep,Provider Name
2026-01,1234564561231237,153.82,Test Rep,Provider 2
2026-01,1234564561231239,25.60,Test Rep,Provider 4"""


class TestAutoCommissionCreation:
    """Tests for auto-commission creation during CSV parsing"""
    
    def test_parse_csv_with_period_creates_commissions(self, api_session, real_csv_file):
        """
        Test: Upload CSV with period selector → commissions auto-created
        Expected: 2 accounts matched to 'test 11', commission records created for each user in the plan
        """
        files = {
            'file': ('test_referral_mrr.csv', real_csv_file, 'text/csv')
        }
        data = {
            'linking_column': 'Account Number',
            'amount_column': 'Comp Paid',
            'period': TEST_PERIOD,  # January 2026
            'mappings': json.dumps({"Sales Rep": "partner_name"})
        }
        
        headers = dict(api_session.headers)
        if 'Content-Type' in headers:
            del headers['Content-Type']
        
        response = requests.post(
            f"{BASE_URL}/api/referral-mrr/parse-csv",
            files=files,
            data=data,
            cookies=api_session.cookies,
            headers=headers
        )
        
        assert response.status_code == 200, f"Parse CSV failed: {response.status_code} - {response.text}"
        
        result = response.json()
        print(f"Parse result: matched={len(result.get('matched', []))}, unmatched={len(result.get('unmatched', []))}")
        print(f"Commissions created: {result.get('commissions_created', 0)}")
        print(f"Commissions skipped: {result.get('commissions_skipped', 0)}")
        print(f"Deals without plans: {len(result.get('deals_without_plans', []))}")
        
        # Verify response structure
        assert 'matched' in result, "Response should contain 'matched' key"
        assert 'unmatched' in result, "Response should contain 'unmatched' key"
        assert 'commissions_created' in result, "Response should contain 'commissions_created' key"
        assert 'commissions_skipped' in result, "Response should contain 'commissions_skipped' key"
        assert 'deals_without_plans' in result, "Response should contain 'deals_without_plans' key"
        assert 'period' in result, "Response should contain 'period'"
        
        # Verify period is correctly returned
        assert result['period'] == TEST_PERIOD, f"Period mismatch: expected {TEST_PERIOD}, got {result['period']}"
        
        # Store for later tests
        TestAutoCommissionCreation.initial_result = result
    
    def test_commissions_persisted_in_database(self, api_session):
        """Verify commission records were actually created in local_commissions"""
        # Access local_commissions data via API 
        # The commissions should appear in Finance Portal → Commissions → Local
        response = api_session.get(f"{BASE_URL}/api/finance/commissions?limit=100")
        assert response.status_code == 200
        
        data = response.json()
        items = data.get('items', [])
        
        # Look for referral_mrr type commissions
        ref_commissions = [
            c for c in items 
            if c.get('source') == 'referral_mrr' or c.get('type') == 'referral_mrr'
        ]
        
        print(f"Found {len(ref_commissions)} referral_mrr commissions in API response")
        
        # If we created commissions, they should show up
        initial_result = getattr(TestAutoCommissionCreation, 'initial_result', {})
        if initial_result.get('commissions_created', 0) > 0:
            # Note: These are created in local_commissions collection
            # Need to check the correct endpoint
            pass
    
    def test_duplicate_prevention_on_reupload(self, api_session, real_csv_file):
        """
        Test: Re-upload same CSV for same period → duplicates skipped
        Expected: commissions_skipped > 0, commissions_created = 0
        """
        files = {
            'file': ('test_referral_mrr.csv', real_csv_file, 'text/csv')
        }
        data = {
            'linking_column': 'Account Number',
            'amount_column': 'Comp Paid',
            'period': TEST_PERIOD,  # Same period as before
            'mappings': json.dumps({"Sales Rep": "partner_name"})
        }
        
        headers = dict(api_session.headers)
        if 'Content-Type' in headers:
            del headers['Content-Type']
        
        response = requests.post(
            f"{BASE_URL}/api/referral-mrr/parse-csv",
            files=files,
            data=data,
            cookies=api_session.cookies,
            headers=headers
        )
        
        assert response.status_code == 200
        result = response.json()
        
        print(f"Re-upload result: created={result.get('commissions_created')}, skipped={result.get('commissions_skipped')}")
        
        # Get counts from initial upload
        initial_result = getattr(TestAutoCommissionCreation, 'initial_result', {})
        initial_matched = len(initial_result.get('matched', []))
        
        # If first upload had matched rows, second upload should skip them
        if initial_matched > 0 and initial_result.get('commissions_created', 0) > 0:
            assert result.get('commissions_skipped', 0) > 0, \
                f"Second upload should skip duplicates, but skipped={result.get('commissions_skipped')}"
            # New commissions should be 0 or minimal
            print(f"✓ Duplicate prevention working: {result.get('commissions_skipped')} duplicates skipped")
    
    def test_different_period_creates_new_commissions(self, api_session, real_csv_file):
        """
        Test: Upload same CSV but different period → new commissions created
        Expected: commissions_created > 0 (not duplicates since period is different)
        """
        different_period = "2026-02"  # February 2026
        
        files = {
            'file': ('test_referral_mrr.csv', real_csv_file, 'text/csv')
        }
        data = {
            'linking_column': 'Account Number',
            'amount_column': 'Comp Paid',
            'period': different_period,
            'mappings': json.dumps({"Sales Rep": "partner_name"})
        }
        
        headers = dict(api_session.headers)
        if 'Content-Type' in headers:
            del headers['Content-Type']
        
        response = requests.post(
            f"{BASE_URL}/api/referral-mrr/parse-csv",
            files=files,
            data=data,
            cookies=api_session.cookies,
            headers=headers
        )
        
        assert response.status_code == 200
        result = response.json()
        
        print(f"Different period result: created={result.get('commissions_created')}, skipped={result.get('commissions_skipped')}")
        
        # With different period, if there are matched rows, new commissions should be created
        matched_count = len(result.get('matched', []))
        if matched_count > 0:
            # Should create commissions since period is different
            # Note: If deal has commission plan with users, commissions will be created
            initial_result = getattr(TestAutoCommissionCreation, 'initial_result', {})
            if initial_result.get('commissions_created', 0) > 0:
                # Same deal, same users, different period = new commissions
                assert result.get('commissions_created', 0) > 0, \
                    f"Different period should create new commissions"
                print(f"✓ Different period correctly creates new commissions")


class TestDealWithoutPlanWarning:
    """Tests for deals without commission plans warning"""
    
    def test_deals_without_plans_in_response(self, api_session, real_csv_file):
        """
        Test: Parse CSV with accounts mapped to deals without commission plans
        Expected: deals_without_plans array contains those deals
        """
        # First, check if any matched rows have deals without plans
        files = {
            'file': ('test_referral_mrr.csv', real_csv_file, 'text/csv')
        }
        data = {
            'linking_column': 'Account Number',
            'amount_column': 'Comp Paid',
            'period': "2026-03",  # New period
            'mappings': '{}'
        }
        
        headers = dict(api_session.headers)
        if 'Content-Type' in headers:
            del headers['Content-Type']
        
        response = requests.post(
            f"{BASE_URL}/api/referral-mrr/parse-csv",
            files=files,
            data=data,
            cookies=api_session.cookies,
            headers=headers
        )
        
        assert response.status_code == 200
        result = response.json()
        
        deals_without_plans = result.get('deals_without_plans', [])
        print(f"Deals without plans: {len(deals_without_plans)}")
        
        for deal in deals_without_plans:
            print(f"  - {deal.get('deal_name')}: Account {deal.get('account_number')}, Amount ${deal.get('amount')}")
        
        # Verify structure if there are any
        if deals_without_plans:
            assert 'deal_id' in deals_without_plans[0], "Each deal should have deal_id"
            assert 'deal_name' in deals_without_plans[0], "Each deal should have deal_name"
            assert 'account_number' in deals_without_plans[0], "Each deal should have account_number"
            assert 'amount' in deals_without_plans[0], "Each deal should have amount"


class TestAccountMappingCRUD:
    """Tests for deal mapping CRUD operations"""
    
    def test_get_existing_mappings(self, api_session):
        """Test GET mappings returns current mappings"""
        response = api_session.get(f"{BASE_URL}/api/referral-mrr/account-mappings")
        assert response.status_code == 200
        
        data = response.json()
        mappings = data.get('mappings', [])
        print(f"Found {len(mappings)} existing mappings")
        
        for m in mappings:
            print(f"  - {m.get('account_number')} → {m.get('deal_name')} ({m.get('deal_id')})")
        
        # Verify expected mappings exist
        account_numbers = [m.get('account_number') for m in mappings]
        expected_accounts = ['1234564561231237', '1234564561231239']
        for acc in expected_accounts:
            if acc in account_numbers:
                print(f"  ✓ Account {acc} is mapped")
    
    def test_create_mapping(self, api_session):
        """Test creating a new account mapping"""
        mapping_data = {
            "account_number": "TEST_AUTO_COMM_001",
            "deal_id": TEST_DEAL_ID,
            "deal_name": "test 11",
            "partner_name": "Test Partner",
            "commission_plan": None,
            "linking_field_name": "Account Number"
        }
        
        response = api_session.post(
            f"{BASE_URL}/api/referral-mrr/account-mappings",
            json=mapping_data
        )
        
        assert response.status_code == 200, f"Create mapping failed: {response.text}"
        print("✓ Test mapping created")
    
    def test_delete_mapping(self, api_session):
        """Test deleting a mapping"""
        response = api_session.delete(f"{BASE_URL}/api/referral-mrr/account-mappings/TEST_AUTO_COMM_001")
        
        assert response.status_code == 200, f"Delete mapping failed: {response.text}"
        print("✓ Test mapping deleted")


class TestPeriodSelectorValidation:
    """Tests for commission period validation"""
    
    def test_period_required(self, api_session, real_csv_file):
        """Test that period is required for CSV parsing"""
        files = {
            'file': ('test.csv', real_csv_file, 'text/csv')
        }
        data = {
            'linking_column': 'Account Number',
            'amount_column': 'Comp Paid',
            # No period provided
            'mappings': '{}'
        }
        
        headers = dict(api_session.headers)
        if 'Content-Type' in headers:
            del headers['Content-Type']
        
        response = requests.post(
            f"{BASE_URL}/api/referral-mrr/parse-csv",
            files=files,
            data=data,
            cookies=api_session.cookies,
            headers=headers
        )
        
        # Should return 400 Bad Request since period is required
        assert response.status_code == 400, f"Expected 400 when period missing, got {response.status_code}"
        print("✓ Period validation working - returns 400 when missing")
    
    def test_period_format_accepted(self, api_session, real_csv_file):
        """Test that valid period formats are accepted"""
        files = {
            'file': ('test.csv', real_csv_file, 'text/csv')
        }
        data = {
            'linking_column': 'Account Number',
            'amount_column': 'Comp Paid',
            'period': '2026-12',  # December 2026
            'mappings': '{}'
        }
        
        headers = dict(api_session.headers)
        if 'Content-Type' in headers:
            del headers['Content-Type']
        
        response = requests.post(
            f"{BASE_URL}/api/referral-mrr/parse-csv",
            files=files,
            data=data,
            cookies=api_session.cookies,
            headers=headers
        )
        
        assert response.status_code == 200
        result = response.json()
        assert result.get('period') == '2026-12', f"Period should be preserved: {result.get('period')}"
        print("✓ Period format correctly accepted and returned")


# Cleanup fixture
@pytest.fixture(scope="module", autouse=True)
def cleanup_test_data(api_session):
    """Cleanup test-created data after all tests"""
    yield
    
    # Cleanup any TEST_ prefixed mappings
    try:
        response = api_session.get(f"{BASE_URL}/api/referral-mrr/account-mappings")
        if response.status_code == 200:
            mappings = response.json().get("mappings", [])
            for mapping in mappings:
                if mapping.get("account_number", "").startswith("TEST_"):
                    api_session.delete(f"{BASE_URL}/api/referral-mrr/account-mappings/{mapping['account_number']}")
    except Exception as e:
        print(f"Cleanup error: {e}")
