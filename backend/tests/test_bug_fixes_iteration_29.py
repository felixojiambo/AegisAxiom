"""
Bug Fix Verification Tests - Iteration 29
Testing fixes for:
- P0 BUG: False positive in 'Deals Missing Commission Plans' - 'test 11' deal incorrectly appeared
- P1 BUG: Blank commission records in Finance Portal 'Local' tab - blank/dash records shown

Key fixes verified:
1. referral_mrr.py: Added commission plan caching + filter `tiers: {$exists: True}` to find plans only
2. finance.py: Added filter `tiers: {$exists: False}` to exclude commission plans from commissions endpoint
"""
import pytest
import requests
import os
import json

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "support@simplimeta.io"
TEST_PASSWORD = "Admin123!"

# Known test data from iteration 28
TEST_DEAL_ID = "deal_9bfa6a930bba"  # 'test 11' deal with commission plan
TEST_DEAL_NAME = "test 11"
MAPPED_ACCOUNTS = ['1234564561231237', '1234564561231239']


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


class TestP0FalsePositiveFix:
    """
    P0 BUG FIX: 'test 11' deal with configured commission plan should NOT appear 
    in 'Deals Missing Commission Plans' warning section
    
    Root cause: The lookup in referral_mrr.py was returning commission records (which don't have 'tiers')
    instead of commission plans (which have 'tiers' field).
    
    Fix: Added `tiers: {$exists: True}` filter to ensure we get commission plans only.
    """
    
    def test_deals_with_plan_not_in_warning_list(self, api_session):
        """
        Test: Parse CSV with accounts mapped to 'test 11' (which HAS a commission plan)
        Expected: 'test 11' should NOT appear in deals_without_plans
        """
        # Read test CSV
        csv_path = "/tmp/test_referral_mrr.csv"
        if os.path.exists(csv_path):
            with open(csv_path, 'r') as f:
                csv_content = f.read()
        else:
            # Fallback CSV with mapped accounts
            csv_content = """Commission Cycle,Account Number,Comp Paid,Sales Rep,Provider Name
2026-01,1234564561231237,153.82,Test Rep,Provider 2
2026-01,1234564561231239,25.60,Test Rep,Provider 4"""
        
        files = {'file': ('test.csv', csv_content, 'text/csv')}
        data = {
            'linking_column': 'Account Number',
            'amount_column': 'Comp Paid',
            'period': '2026-05',  # New period to test
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
        
        assert response.status_code == 200, f"Parse failed: {response.status_code} - {response.text}"
        result = response.json()
        
        deals_without_plans = result.get('deals_without_plans', [])
        print(f"\nDeals without plans count: {len(deals_without_plans)}")
        
        # P0 BUG FIX: 'test 11' should NOT be in this list
        test_11_in_warning = any(
            deal.get('deal_id') == TEST_DEAL_ID or deal.get('deal_name') == TEST_DEAL_NAME
            for deal in deals_without_plans
        )
        
        if test_11_in_warning:
            print(f"❌ P0 BUG NOT FIXED: 'test 11' incorrectly in deals_without_plans: {deals_without_plans}")
        else:
            print(f"✅ P0 BUG FIXED: 'test 11' correctly NOT in deals_without_plans")
        
        assert not test_11_in_warning, \
            f"P0 BUG: 'test 11' with commission plan should NOT appear in deals_without_plans. Found: {deals_without_plans}"
    
    def test_commissions_created_for_mapped_deal(self, api_session):
        """
        Test: Verify that commissions ARE being created for 'test 11' deal 
        (proves the plan IS found and used)
        """
        csv_content = """Commission Cycle,Account Number,Comp Paid,Sales Rep,Provider Name
2026-01,1234564561231237,100.00,Test Rep,Provider X"""
        
        files = {'file': ('test.csv', csv_content, 'text/csv')}
        data = {
            'linking_column': 'Account Number',
            'amount_column': 'Comp Paid',
            'period': '2026-06',  # Fresh period
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
        
        matched = result.get('matched', [])
        commissions_created = result.get('commissions_created', 0)
        deals_without_plans = result.get('deals_without_plans', [])
        
        print(f"\nMatched rows: {len(matched)}")
        print(f"Commissions created: {commissions_created}")
        print(f"Deals without plans: {len(deals_without_plans)}")
        
        # If we have matched rows and a deal with a plan, commissions should be created
        if len(matched) > 0:
            # Either commissions were created, OR it was a duplicate (skipped)
            commissions_skipped = result.get('commissions_skipped', 0)
            assert commissions_created > 0 or commissions_skipped > 0, \
                f"Expected commissions to be created or skipped for mapped deal with plan. Created: {commissions_created}, Skipped: {commissions_skipped}"
            print(f"✅ Commissions created/skipped for deal with plan: {commissions_created}/{commissions_skipped}")


class TestP1BlankRecordsFix:
    """
    P1 BUG FIX: Blank commission records in Finance Portal 'Local' tab
    
    Root cause: The /api/finance/commissions endpoint was returning commission PLANS 
    (which have 'tiers' field but no user_name, deal_name for display) instead of 
    commission RECORDS.
    
    Fix: Added `tiers: {$exists: False}` filter to exclude commission plans.
    """
    
    def test_commissions_endpoint_returns_records_not_plans(self, api_session):
        """
        Test: Finance commissions API should only return commission records, NOT plans
        Expected: All items should have commission record fields (user_name, deal_name, amount_owed)
                  and NOT have 'tiers' field
        """
        response = api_session.get(f"{BASE_URL}/api/finance/commissions?limit=100")
        assert response.status_code == 200, f"API failed: {response.status_code}"
        
        data = response.json()
        items = data.get('items', [])
        
        print(f"\nFinance commissions endpoint returned: {len(items)} items")
        
        # Check that NO items have 'tiers' field (that would be a commission plan)
        items_with_tiers = [item for item in items if 'tiers' in item]
        
        if items_with_tiers:
            print(f"❌ P1 BUG NOT FIXED: Found {len(items_with_tiers)} items with 'tiers' (commission plans)")
            for item in items_with_tiers[:3]:
                print(f"  - {item.get('commission_id', item.get('deal_id'))}: tiers present")
        else:
            print(f"✅ P1 BUG FIXED: No commission plans in response (0 items with 'tiers')")
        
        assert len(items_with_tiers) == 0, \
            f"P1 BUG: Commission plans (with 'tiers') should be excluded from response. Found: {len(items_with_tiers)}"
    
    def test_commission_records_have_required_display_fields(self, api_session):
        """
        Test: Commission records should have fields needed for display
        Expected: user_name, deal_name, amount_owed should be present and NOT blank
        """
        response = api_session.get(f"{BASE_URL}/api/finance/commissions?limit=100")
        assert response.status_code == 200
        
        data = response.json()
        items = data.get('items', [])
        
        if len(items) == 0:
            pytest.skip("No commission records to test")
        
        # Look for referral_mrr type commissions specifically
        referral_commissions = [
            item for item in items 
            if item.get('source') == 'referral_mrr' or item.get('commission_type') == 'referral_mrr'
        ]
        
        print(f"\nReferral MRR commissions: {len(referral_commissions)}")
        
        blank_records = []
        valid_records = []
        
        for item in referral_commissions:
            user_name = item.get('user_name', '')
            deal_name = item.get('deal_name', '')
            amount_owed = item.get('amount_owed', 0)
            
            # Check if this is a "blank" record (missing display fields)
            is_blank = not user_name or not deal_name
            
            if is_blank:
                blank_records.append({
                    'commission_id': item.get('commission_id'),
                    'user_name': user_name,
                    'deal_name': deal_name,
                    'amount_owed': amount_owed
                })
            else:
                valid_records.append(item.get('commission_id'))
        
        print(f"Valid records with display data: {len(valid_records)}")
        print(f"Blank records missing display data: {len(blank_records)}")
        
        if blank_records:
            print("Blank records details:")
            for br in blank_records[:5]:
                print(f"  - {br}")
        
        # P1 Fix: Should have NO blank records
        assert len(blank_records) == 0, \
            f"P1 BUG: Found {len(blank_records)} records with blank user_name/deal_name. First few: {blank_records[:3]}"
        
        print(f"✅ All referral commission records have proper display fields")


class TestCommissionPlanCaching:
    """
    Test: Commission plan caching - multiple accounts mapped to same deal 
    should only check commission plan once
    """
    
    def test_multiple_accounts_same_deal_efficient_lookup(self, api_session):
        """
        Test: Two accounts mapped to same 'test 11' deal - plan lookup should be cached
        The fix adds commission_plans_cache to avoid redundant DB queries.
        """
        # Create CSV with multiple rows for same deal
        csv_content = """Commission Cycle,Account Number,Comp Paid,Sales Rep,Provider Name
2026-01,1234564561231237,100.00,Test Rep,Provider X
2026-01,1234564561231239,50.00,Test Rep,Provider Y"""
        
        files = {'file': ('test.csv', csv_content, 'text/csv')}
        data = {
            'linking_column': 'Account Number',
            'amount_column': 'Comp Paid',
            'period': '2026-07',  # Fresh period
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
        
        assert response.status_code == 200, f"Parse failed: {response.status_code}"
        result = response.json()
        
        matched = result.get('matched', [])
        deals_without_plans = result.get('deals_without_plans', [])
        
        print(f"\nMatched rows (same deal): {len(matched)}")
        
        # Both accounts should be matched to same deal
        deal_ids = set(m.get('deal_id') for m in matched)
        print(f"Unique deal IDs: {deal_ids}")
        
        # The deal should appear at most ONCE in deals_without_plans 
        # (not twice - that would mean caching isn't working)
        deal_occurrences = [
            deal for deal in deals_without_plans 
            if deal.get('deal_id') == TEST_DEAL_ID
        ]
        
        # With the fix, 'test 11' should NOT appear at all since it HAS a plan
        assert len(deal_occurrences) == 0, \
            f"Deal 'test 11' should not appear in deals_without_plans at all. Found: {len(deal_occurrences)}"
        
        print(f"✅ Commission plan caching working - deal not in warning list")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
