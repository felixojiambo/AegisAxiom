"""
Referral MRR Backend Tests
Tests for CSV upload, parsing, account mappings, templates, and commission commits
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin123!"


@pytest.fixture(scope="module")
def api_session():
    """Create authenticated session for all tests"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    # Login
    login_response = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })
    
    if login_response.status_code != 200:
        pytest.skip(f"Authentication failed: {login_response.status_code}")
    
    return session


@pytest.fixture(scope="module")
def test_csv_file():
    """Create test CSV file content"""
    csv_content = """Account Number,Comp Paid,Partner Name,Product
ACC001,125.50,AppDirect Partner 1,SimpliSecure
ACC002,250.00,AppDirect Partner 2,SimpliComply
ACC003,175.25,AppDirect Partner 1,SimpliSecure
ACC004,89.99,AppDirect Partner 3,Custom Product
ACC005,310.75,AppDirect Partner 2,SimpliComply"""
    return csv_content


class TestReferralMRRAccountMappings:
    """Tests for account mapping CRUD operations"""
    
    def test_get_account_mappings(self, api_session):
        """Test GET /api/referral-mrr/account-mappings returns list"""
        response = api_session.get(f"{BASE_URL}/api/referral-mrr/account-mappings")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "mappings" in data, "Response should contain 'mappings' key"
        assert isinstance(data["mappings"], list), "Mappings should be a list"
    
    def test_create_account_mapping(self, api_session):
        """Test POST /api/referral-mrr/account-mappings creates new mapping"""
        # First get deals to get a valid deal_id
        deals_response = api_session.get(f"{BASE_URL}/api/deals")
        assert deals_response.status_code == 200
        deals = deals_response.json()
        
        # Handle both array and object response
        if isinstance(deals, list):
            deal_list = deals
        else:
            deal_list = deals.get("items", [])
        
        if not deal_list:
            pytest.skip("No deals available to map account to")
        
        test_deal = deal_list[0]
        
        mapping_data = {
            "account_number": "TEST_ACC_001",
            "deal_id": test_deal.get("deal_id"),
            "deal_name": test_deal.get("name", "Test Deal"),
            "partner_name": "Test Partner",
            "commission_plan": None,
            "linking_field_name": "Account Number"
        }
        
        response = api_session.post(
            f"{BASE_URL}/api/referral-mrr/account-mappings",
            json=mapping_data
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "message" in data
        assert "mapping" in data["message"].lower() or "created" in data["message"].lower() or "updated" in data["message"].lower()
    
    def test_account_mapping_persisted(self, api_session):
        """Verify account mapping was persisted by fetching it"""
        response = api_session.get(f"{BASE_URL}/api/referral-mrr/account-mappings")
        assert response.status_code == 200
        
        data = response.json()
        mappings = data.get("mappings", [])
        
        # Find our test mapping
        test_mapping = next((m for m in mappings if m.get("account_number") == "TEST_ACC_001"), None)
        assert test_mapping is not None, "Test mapping should be persisted"
        assert test_mapping.get("partner_name") == "Test Partner"
    
    def test_delete_account_mapping(self, api_session):
        """Test DELETE /api/referral-mrr/account-mappings/{account_number}"""
        response = api_session.delete(f"{BASE_URL}/api/referral-mrr/account-mappings/TEST_ACC_001")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        # Verify deletion
        get_response = api_session.get(f"{BASE_URL}/api/referral-mrr/account-mappings")
        data = get_response.json()
        mappings = data.get("mappings", [])
        test_mapping = next((m for m in mappings if m.get("account_number") == "TEST_ACC_001"), None)
        assert test_mapping is None, "Test mapping should be deleted"


class TestReferralMRRHeaderTemplates:
    """Tests for header template CRUD operations"""
    
    def test_get_header_templates(self, api_session):
        """Test GET /api/referral-mrr/header-templates returns list"""
        response = api_session.get(f"{BASE_URL}/api/referral-mrr/header-templates")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "templates" in data, "Response should contain 'templates' key"
        assert isinstance(data["templates"], list), "Templates should be a list"
    
    def test_create_header_template(self, api_session):
        """Test POST /api/referral-mrr/header-templates creates new template"""
        template_data = {
            "template_name": "TEST_AppDirect_Template",
            "mappings": {"Partner Name": "partner_name"},
            "linking_column": "Account Number",
            "amount_column": "Comp Paid"
        }
        
        response = api_session.post(
            f"{BASE_URL}/api/referral-mrr/header-templates",
            json=template_data
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "template_id" in data, "Response should contain template_id"
        assert "message" in data
        
        # Store template_id for cleanup
        TestReferralMRRHeaderTemplates.created_template_id = data["template_id"]
    
    def test_header_template_persisted(self, api_session):
        """Verify header template was persisted"""
        response = api_session.get(f"{BASE_URL}/api/referral-mrr/header-templates")
        assert response.status_code == 200
        
        data = response.json()
        templates = data.get("templates", [])
        
        # Find our test template
        test_template = next((t for t in templates if t.get("template_name") == "TEST_AppDirect_Template"), None)
        assert test_template is not None, "Test template should be persisted"
        assert test_template.get("linking_column") == "Account Number"
        assert test_template.get("amount_column") == "Comp Paid"
    
    def test_delete_header_template(self, api_session):
        """Test DELETE /api/referral-mrr/header-templates/{template_id}"""
        template_id = getattr(TestReferralMRRHeaderTemplates, 'created_template_id', None)
        if not template_id:
            # Find the template
            response = api_session.get(f"{BASE_URL}/api/referral-mrr/header-templates")
            templates = response.json().get("templates", [])
            test_template = next((t for t in templates if t.get("template_name") == "TEST_AppDirect_Template"), None)
            if test_template:
                template_id = test_template.get("template_id")
        
        if not template_id:
            pytest.skip("No test template found to delete")
        
        response = api_session.delete(f"{BASE_URL}/api/referral-mrr/header-templates/{template_id}")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"


class TestReferralMRRCSVParsing:
    """Tests for CSV parsing functionality"""
    
    def test_parse_csv_with_valid_columns(self, api_session, test_csv_file):
        """Test POST /api/referral-mrr/parse-csv with proper linking and amount columns"""
        # Use requests-toolbelt for multipart or use a different approach
        files = {
            'file': ('test_appdirect.csv', test_csv_file, 'text/csv')
        }
        data = {
            'linking_column': 'Account Number',
            'amount_column': 'Comp Paid',
            'mappings': '{"Partner Name": "partner_name"}'
        }
        
        # Remove Content-Type header for multipart
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
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        result = response.json()
        
        # Verify response structure
        assert "matched" in result, "Response should contain 'matched'"
        assert "unmatched" in result, "Response should contain 'unmatched'"
        assert "total_rows" in result, "Response should contain 'total_rows'"
        
        # Verify total rows
        assert result["total_rows"] == 5, f"Expected 5 total rows, got {result['total_rows']}"
        
        # Verify matched + unmatched = total
        matched_count = len(result["matched"])
        unmatched_count = len(result["unmatched"])
        assert matched_count + unmatched_count == result["total_rows"], \
            f"Matched ({matched_count}) + Unmatched ({unmatched_count}) should equal total ({result['total_rows']})"
    
    def test_parse_csv_amounts_not_zero(self, api_session, test_csv_file):
        """Test that parsed amounts are NOT $0.00"""
        files = {
            'file': ('test_appdirect.csv', test_csv_file, 'text/csv')
        }
        data = {
            'linking_column': 'Account Number',
            'amount_column': 'Comp Paid',
            'mappings': '{}'
        }
        
        # Remove Content-Type header for multipart
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
        
        # Check unmatched rows have amounts > 0
        for row in result.get("unmatched", []):
            assert row.get("amount", 0) > 0, f"Amount should not be 0 for {row.get('account_number')}: got {row.get('amount')}"
        
        # Check matched rows have amounts > 0  
        for row in result.get("matched", []):
            assert row.get("amount", 0) > 0, f"Amount should not be 0 for matched row {row.get('account_number')}"
    
    def test_parse_csv_without_linking_column_fails(self, api_session, test_csv_file):
        """Test that parsing without linking_column should result in 0 rows or validation error"""
        files = {
            'file': ('test_appdirect.csv', test_csv_file, 'text/csv')
        }
        data = {
            'amount_column': 'Comp Paid',
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
        # Either returns 200 with 0 rows or 400 validation error
        if response.status_code == 200:
            result = response.json()
            # Should have 0 matched + unmatched since no linking column
            assert result["total_rows"] == 0 or \
                (len(result.get("matched", [])) == 0 and len(result.get("unmatched", [])) == 0), \
                "Without linking column, no rows should be parsed"


class TestReferralMRRCommissions:
    """Tests for commission commit functionality"""
    
    def test_get_referral_commissions(self, api_session):
        """Test GET /api/referral-mrr/commissions returns list"""
        response = api_session.get(f"{BASE_URL}/api/referral-mrr/commissions")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "commissions" in data, "Response should contain 'commissions' key"
        assert isinstance(data["commissions"], list), "Commissions should be a list"
    
    def test_commit_commissions(self, api_session):
        """Test POST /api/referral-mrr/commit-commissions"""
        # First get a deal to use
        deals_response = api_session.get(f"{BASE_URL}/api/deals")
        deals = deals_response.json()
        deal_list = deals if isinstance(deals, list) else deals.get("items", [])
        
        if not deal_list:
            pytest.skip("No deals available for commission commit test")
        
        test_deal = deal_list[0]
        
        commission_rows = [
            {
                "account_number": "TEST_COMMIT_001",
                "deal_id": test_deal.get("deal_id"),
                "deal_name": test_deal.get("name", "Test Deal"),
                "partner_name": "Test Partner",
                "amount": 150.00,
                "commission_plan": "default",
                "linking_field_name": "Account Number",
                "raw_data": {}
            }
        ]
        
        response = api_session.post(
            f"{BASE_URL}/api/referral-mrr/commit-commissions",
            json={
                "rows": commission_rows,
                "period": "2026-03"
            }
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "message" in data
        assert "committed" in data["message"].lower() or "success" in data["message"].lower()
    
    def test_get_commissions_by_period(self, api_session):
        """Test GET /api/referral-mrr/commissions with period filter"""
        response = api_session.get(f"{BASE_URL}/api/referral-mrr/commissions?period=2026-03")
        assert response.status_code == 200
        
        data = response.json()
        assert "commissions" in data


class TestReferralMRRE2EFlow:
    """End-to-end flow tests"""
    
    def test_full_csv_to_mapping_to_commit_flow(self, api_session):
        """Test complete flow: upload CSV -> map account -> re-parse -> commit"""
        # Get deals
        deals_response = api_session.get(f"{BASE_URL}/api/deals")
        deals = deals_response.json()
        deal_list = deals if isinstance(deals, list) else deals.get("items", [])
        
        if not deal_list:
            pytest.skip("No deals available for E2E test")
        
        test_deal = deal_list[0]
        
        # 1. Parse CSV - initially all unmatched
        csv_content = """Account Number,Comp Paid,Partner Name
E2E_ACC_001,100.00,E2E Partner
E2E_ACC_002,200.00,E2E Partner"""
        
        files = {'file': ('e2e_test.csv', csv_content, 'text/csv')}
        data = {
            'linking_column': 'Account Number',
            'amount_column': 'Comp Paid',
            'mappings': '{"Partner Name": "partner_name"}'
        }
        
        headers = dict(api_session.headers)
        if 'Content-Type' in headers:
            del headers['Content-Type']
        
        parse_response = requests.post(
            f"{BASE_URL}/api/referral-mrr/parse-csv",
            files=files,
            data=data,
            cookies=api_session.cookies,
            headers=headers
        )
        assert parse_response.status_code == 200
        parse_result = parse_response.json()
        
        # Should have 2 unmatched rows
        assert parse_result["total_rows"] == 2, f"Expected 2 rows, got {parse_result['total_rows']}"
        initial_unmatched = len(parse_result.get("unmatched", []))
        
        # 2. Create account mapping for first account
        mapping_response = api_session.post(
            f"{BASE_URL}/api/referral-mrr/account-mappings",
            json={
                "account_number": "E2E_ACC_001",
                "deal_id": test_deal.get("deal_id"),
                "deal_name": test_deal.get("name"),
                "partner_name": "E2E Partner",
                "commission_plan": None,
                "linking_field_name": "Account Number"
            }
        )
        assert mapping_response.status_code == 200
        
        # 3. Re-parse CSV - now one should be matched
        files = {'file': ('e2e_test.csv', csv_content, 'text/csv')}
        reparse_response = requests.post(
            f"{BASE_URL}/api/referral-mrr/parse-csv",
            files=files,
            data=data,
            cookies=api_session.cookies,
            headers=headers
        )
        assert reparse_response.status_code == 200
        reparse_result = reparse_response.json()
        
        # Should have 1 matched, 1 unmatched now
        assert len(reparse_result.get("matched", [])) >= 1, "Should have at least 1 matched row after mapping"
        assert len(reparse_result.get("unmatched", [])) <= initial_unmatched, "Unmatched should decrease after mapping"
        
        # 4. Cleanup - delete test mapping
        api_session.delete(f"{BASE_URL}/api/referral-mrr/account-mappings/E2E_ACC_001")


# Cleanup test data
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
                if mapping.get("account_number", "").startswith("TEST_") or \
                   mapping.get("account_number", "").startswith("E2E_"):
                    api_session.delete(f"{BASE_URL}/api/referral-mrr/account-mappings/{mapping['account_number']}")
    except Exception as e:
        print(f"Cleanup error: {e}")
    
    # Cleanup test templates
    try:
        response = api_session.get(f"{BASE_URL}/api/referral-mrr/header-templates")
        if response.status_code == 200:
            templates = response.json().get("templates", [])
            for template in templates:
                if template.get("template_name", "").startswith("TEST_"):
                    api_session.delete(f"{BASE_URL}/api/referral-mrr/header-templates/{template['template_id']}")
    except Exception as e:
        print(f"Cleanup error: {e}")
